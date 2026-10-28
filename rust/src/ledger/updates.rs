//! Updates without the user, layer 3 (SPRINT-OCT24.md, "Updates without the user"): rb itself.
//!
//! - rb installed as a tool, in an environment of its own (`uv tool install rabbit-brain`, `pipx install
//!   rabbit-brain`), updates itself: at most once a day a background rb asks PyPI's public JSON for the newest version,
//!   and when it is newer runs the same installer's upgrade (`uv tool upgrade`, `pipx upgrade`). The next command runs
//!   the new rb; a screen that stays open says "✔ Update installed · Restart to update".
//! - rb installed with pip, in a project's environment or conda: rb never touches that environment. It says one quiet
//!   line, at most once a day: "rb 0.7.0 is out: pip install -U rabbit-brain".
//! - Neither runs in CI, in an agent session, under DO_NOT_TRACK, in the tests, or after `rb config auto-update off`.
//!   The check sends nothing about the user (a GET of a public address), and a failed check or upgrade is silent and
//!   changes no command's result.
//!
//! What rb knows of it is kept in `update.json` in rb's settings folder (`hub::config_dir`), beside the credentials.

use crate::pyjson;
use crate::value::{Dict, Value};
use std::path::{Path, PathBuf};
use std::time::Duration;

const DAY: i64 = 24 * 60 * 60;
const INDEX: &str = "https://pypi.org/pypi/rabbit-brain/json";
/// The hidden command a background check runs as: no help text names it.
pub const WORKER: &str = "__update";
/// The CI variables telemetry reads (telemetry.rs CI_VARIABLES), read the same way.
const CI_VARIABLES: [&str; 10] = [
    "CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "TF_BUILD", "JENKINS_URL", "TEAMCITY_VERSION", "CODEBUILD_BUILD_ARN",
    "bamboo_planKey", "BUILDER_OUTPUT",
];

/// How this rb was installed, as far as its updates go.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Install {
    /// `uv tool install`: an environment of its own, with uv's receipt in it.
    Uv,
    /// `pipx install`: an environment of its own, with pipx's metadata in it.
    Pipx,
    /// Anything else: pip in a project's environment, conda, a build from source.
    Pip,
}

/// How the rb at `exe` was installed: the environment it runs from (the folder above its `bin/` or `Scripts/`) holds
/// uv's receipt for a uv tool, pipx's metadata for a pipx one.
pub fn install_of(exe: &Path) -> Install {
    tool_env(exe).map_or(Install::Pip, |(kind, _)| kind)
}

/// The tool environment the rb at `exe` belongs to, with its kind: the folder that holds the installer's mark, found
/// above the program (a link to it is followed), or, where the installer copied the program out to its folder of
/// programs (uv and pipx do on Windows), the default tool environment whose own program has the same bytes.
pub fn tool_env(exe: &Path) -> Option<(Install, PathBuf)> {
    let exe = std::fs::canonicalize(exe).unwrap_or_else(|_| exe.to_path_buf());
    // the program sits in the environment's bin/ (Scripts/ on Windows), and only there: a mark further up (a project
    // that holds a file named like the installer's) makes nothing a tool
    let in_bin = exe.parent().filter(|b| matches!(b.file_name().and_then(|n| n.to_str()), Some("bin" | "Scripts")));
    if let Some(env) = in_bin.and_then(Path::parent) {
        if let Some(kind) = marked(env) {
            return Some((kind, env.to_path_buf()));
        }
    }
    let ours = std::fs::read(&exe).ok()?;
    let name = exe.file_name()?;
    for env in default_tool_envs() {
        let Some(kind) = marked(&env) else { continue };
        for bin in ["bin", "Scripts"] {
            if std::fs::read(env.join(bin).join(name)).map_or(false, |theirs| theirs == ours) {
                return Some((kind, env));
            }
        }
    }
    None
}

/// Whether `env` is rabbit-brain's own tool environment, and whose: a Python environment (its pyvenv.cfg) named
/// rabbit-brain in uv's folder of tools or pipx's folder of environments, whose installer's mark names rabbit-brain.
fn marked(env: &Path) -> Option<Install> {
    let name = |p: &Path| p.file_name().and_then(|n| n.to_str()).map(str::to_string);
    if name(env).as_deref() != Some("rabbit-brain") || !env.join("pyvenv.cfg").is_file() {
        return None;
    }
    let names_us = |file: &str| std::fs::read_to_string(env.join(file)).map_or(false, |t| t.contains("rabbit-brain") || t.contains("rabbit_brain"));
    match env.parent().and_then(name).as_deref() {
        Some("tools") if names_us("uv-receipt.toml") => Some(Install::Uv),
        Some("venvs") if names_us("pipx_metadata.json") => Some(Install::Pipx),
        _ => None,
    }
}

/// The installer to run, by its absolute path: the first on PATH that is not in a repository's working tree (a
/// project's own folder of programs, or one an environment put on PATH from it), so a checkout never chooses it.
fn installer(program: &str) -> Option<PathBuf> {
    installer_on(program, &std::env::var_os("PATH")?)
}

fn installer_on(program: &str, path: &std::ffi::OsStr) -> Option<PathBuf> {
    let names: Vec<String> = if cfg!(windows) { vec![format!("{program}.exe"), format!("{program}.cmd")] } else { vec![program.to_string()] };
    for dir in std::env::split_paths(path) {
        if !dir.is_absolute() || dir.ancestors().any(|a| a.join(".git").exists() || a.join(".rb").exists()) {
            continue;
        }
        for n in &names {
            let p = dir.join(n);
            if p.is_file() {
                return Some(p);
            }
        }
    }
    None
}

/// Where uv and pipx keep rabbit-brain's environment when nothing says otherwise, and where their variables say.
fn default_tool_envs() -> Vec<PathBuf> {
    let var = |k: &str| std::env::var_os(k).filter(|v| !v.is_empty()).map(PathBuf::from);
    let home = var("HOME").or_else(|| var("USERPROFILE"));
    let mut dirs: Vec<PathBuf> = Vec::new();
    dirs.extend(var("UV_TOOL_DIR"));
    dirs.extend(var("XDG_DATA_HOME").map(|d| d.join("uv").join("tools")));
    dirs.extend(var("APPDATA").map(|d| d.join("uv").join("data").join("tools")));
    dirs.extend(home.as_ref().map(|h| h.join(".local").join("share").join("uv").join("tools")));
    dirs.extend(var("PIPX_HOME").map(|d| d.join("venvs")));
    dirs.extend(home.as_ref().map(|h| h.join(".local").join("pipx").join("venvs")));
    dirs.extend(home.as_ref().map(|h| h.join(".local").join("share").join("pipx").join("venvs")));
    dirs.into_iter().map(|d| d.join("rabbit-brain")).collect()
}

/// Why updates are off here, or None when they run.
pub fn off_reason(get: impl Fn(&str) -> Option<String>, auto_update: bool, agent_session: bool) -> Option<&'static str> {
    let on = |k: &str| get(k).map_or(false, |v| !v.is_empty() && !matches!(v.to_ascii_lowercase().as_str(), "0" | "false" | "no"));
    let off = |k: &str| get(k).map_or(false, |v| matches!(v.trim().to_ascii_lowercase().as_str(), "0" | "false" | "no" | "off"));
    if !auto_update || off("RB_AUTO_UPDATE") {
        Some("config")
    } else if on("DO_NOT_TRACK") {
        Some("do_not_track")
    } else if CI_VARIABLES.iter().any(|k| on(k)) {
        Some("ci")
    } else if agent_session {
        Some("agent_session")
    } else if get("PYTEST_CURRENT_TEST").is_some() && !on("RB_TEST_UPDATES") {
        Some("test")
    } else {
        None
    }
}

/// What rb keeps of its updates.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct State {
    pub auto_update: bool,
    /// When the last check started (seconds since the epoch).
    pub checked_at: i64,
    /// The newest version the last check found.
    pub latest: Option<String>,
    /// The version an upgrade installed, which the next command runs.
    pub installed: Option<String>,
    /// The tool environment it was installed in: only an rb of that environment says it.
    pub installed_in: Option<String>,
    /// Whether the file could not be read as rb wrote it: then nothing is checked or upgraded, a setting of the user's
    /// being unknown.
    pub unreadable: bool,
    /// When the pip line was last said.
    pub said_at: i64,
}

/// Where rb keeps what it knows of its updates, or None where that would be inside a record (a `.rb` folder however
/// it is spelled, through `..` or a link): the same rule as telemetry's state, so nothing of it lands in research.
fn path() -> Option<PathBuf> {
    use std::path::Component;
    let dir = PathBuf::from(crate::hub::config_dir());
    let in_record = |p: &Path| {
        p.components().any(|c| match c {
            Component::Normal(n) => n.to_str().map_or(false, |s| (if cfg!(windows) { s.trim_end_matches(['.', ' ']) } else { s }).eq_ignore_ascii_case(".rb")),
            _ => false,
        })
    };
    if !dir.is_absolute() || in_record(&dir) || dir.components().any(|c| c == Component::ParentDir) {
        return None;
    }
    let ancestor = dir.ancestors().find(|p| p.exists())?;
    if in_record(&std::fs::canonicalize(ancestor).ok()?) {
        return None;
    }
    Some(dir.join("update.json"))
}

pub fn read() -> State {
    let mut s = State { auto_update: true, ..State::default() };
    let Some(p) = path() else {
        s.unreadable = true; // no place outside a record to keep it: nothing runs
        return s;
    };
    let Ok(text) = std::fs::read_to_string(p) else { return s };
    let Ok(Value::Dict(d)) = pyjson::loads(&text) else {
        s.unreadable = true;
        return s;
    };
    let text_of = |k: &str| d.get(k).and_then(Value::as_str).filter(|v| !v.is_empty()).map(str::to_string);
    s.auto_update = !matches!(d.get("auto_update"), Some(Value::Bool(false)));
    s.checked_at = d.get("checked_at").and_then(Value::as_i64).unwrap_or(0);
    s.said_at = d.get("said_at").and_then(Value::as_i64).unwrap_or(0);
    s.latest = text_of("latest");
    s.installed = text_of("installed");
    s.installed_in = text_of("installed_in");
    s
}

/// Run `f` holding rb's lock on its update state, across processes: each read, check and claim is one step, so two
/// commands together claim the day's check (or the day's line) once. None when the lock cannot be had in a moment
/// (another rb holds it): then this one does nothing. A lock left by an rb that ended mid-step is taken after 2 minutes.
pub fn locked<T>(f: impl FnOnce() -> T) -> Option<T> {
    let lock = path()?.with_extension("lock");
    std::fs::create_dir_all(lock.parent()?).ok()?;
    hold(&lock, f)
}

/// The lock at `lock` (a file made with create_new), held while `f` runs.
pub fn hold<T>(lock: &Path, f: impl FnOnce() -> T) -> Option<T> {
    let until = std::time::Instant::now() + Duration::from_secs(2);
    loop {
        match std::fs::OpenOptions::new().write(true).create_new(true).open(lock) {
            Ok(_) => break,
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                let stale = std::fs::metadata(lock).and_then(|m| m.modified()).ok().and_then(|t| t.elapsed().ok()).map_or(false, |age| age > Duration::from_secs(120));
                if stale {
                    let _ = std::fs::remove_file(lock);
                    continue;
                }
                if std::time::Instant::now() >= until {
                    return None;
                }
                std::thread::sleep(Duration::from_millis(25));
            }
            Err(_) => return None,
        }
    }
    let got = f();
    let _ = std::fs::remove_file(lock);
    Some(got)
}

pub fn write(s: &State) -> bool {
    let Some(p) = path() else { return false };
    if let Some(dir) = p.parent() {
        if std::fs::create_dir_all(dir).is_err() {
            return false;
        }
    }
    let mut d = Dict::new();
    d.insert("auto_update".into(), Value::Bool(s.auto_update));
    d.insert("checked_at".into(), Value::Int(s.checked_at));
    d.insert("latest".into(), s.latest.clone().map_or(Value::Null, Value::Str));
    d.insert("installed".into(), s.installed.clone().map_or(Value::Null, Value::Str));
    d.insert("installed_in".into(), s.installed_in.clone().map_or(Value::Null, Value::Str));
    d.insert("said_at".into(), Value::Int(s.said_at));
    let text = pyjson::dumps_with(&Value::Dict(d), &pyjson::Dumps::indent(1)).unwrap_or_default();
    crate::ledger::write_atomic(&p, text.as_bytes()).is_ok()
}

fn now() -> i64 {
    crate::pytime::now_dt().timestamp()
}

/// A version's release numbers (`0.7.0` is [0, 7, 0]), or None for a pre-release or a development build, which rb
/// never offers. A local part (`+local`) and a post-release (`.post1`) are left out: a post-release is its release.
pub fn release(v: &str) -> Option<Vec<u64>> {
    let (nums, phase) = key(v)?;
    (phase >= 1).then_some(nums)
}

/// A version as it orders: its release numbers, then where it stands beside that release (0 a pre-release or a
/// development build of it, 1 the release, 2 and on a post-release of it). None when it has no release numbers.
fn key(v: &str) -> Option<(Vec<u64>, u64)> {
    let v = v.trim().split('+').next().unwrap_or("").to_ascii_lowercase();
    let end = v.find(|c: char| !c.is_ascii_digit() && c != '.').unwrap_or(v.len());
    let (head, tail) = v.split_at(end);
    let nums: Option<Vec<u64>> = head.trim_end_matches('.').split('.').map(|p| p.parse::<u64>().ok()).collect();
    let mut nums = nums.filter(|n| !n.is_empty())?;
    while nums.len() > 1 && nums.last() == Some(&0) {
        nums.pop(); // 0.7 and 0.7.0 are one release
    }
    let tail = tail.trim_start_matches(['.', '-', '_']);
    let phase = if tail.is_empty() {
        1
    } else if let Some(n) = tail.strip_prefix("post") {
        2 + n.trim_start_matches(['.', '-', '_']).parse::<u64>().unwrap_or(0)
    } else {
        0
    };
    Some((nums, phase))
}

/// Whether `latest` is a release (never a pre-release) newer than `running`.
pub fn newer(latest: &str, running: &str) -> bool {
    let Some((l, lp)) = key(latest).filter(|(_, p)| *p >= 1) else { return false };
    let Some((r, rp)) = key(running) else { return true };
    (l, lp) > (r, rp)
}

/// The pip line, when there is one to say now: a newer release than this rb, said at most once a day.
pub fn pip_line(s: &State, running: &str, at: i64) -> Option<String> {
    let latest = s.latest.as_deref().filter(|l| newer(l, running))?;
    (at - s.said_at >= DAY).then(|| format!("rb {latest} is out: pip install -U rabbit-brain"))
}

/// The line a screen that stays open says once an upgrade installed a newer rb than the one it started as
/// (`started`: the version the screen read as it opened; the program on disk may be the new one already), in the tool
/// environment it runs from.
pub fn installed_line(started: &str) -> Option<String> {
    let s = read();
    let here = std::env::current_exe().ok().and_then(|e| tool_env(&e)).map(|(_, env)| crate::sys::slashed(&env));
    let same_env = s.installed_in.is_some() && s.installed_in == here;
    s.installed.filter(|v| same_env && newer(v, started)).map(|_| "✔ Update installed · Restart to update".to_string())
}

/// An agent session as rb confirm reads one: a runtime's sign, or RB_ACTOR naming an agent.
pub fn agent_session() -> bool {
    crate::actor::agent_session_sign().is_some() || std::env::var("RB_ACTOR").map_or(false, |a| a.trim().starts_with("agent:"))
}

/// A CI job, by the variables telemetry reads.
pub fn ci() -> bool {
    CI_VARIABLES.iter().any(|k| std::env::var(k).map_or(false, |v| !v.is_empty() && !matches!(v.to_ascii_lowercase().as_str(), "0" | "false" | "no")))
}

/// Whether updates run here, read from this process's environment and the user's setting.
pub fn running_here(s: &State) -> bool {
    !s.unreadable && off_reason(|k| std::env::var(k).ok(), s.auto_update, agent_session()).is_none()
}

/// A process rb started in the background (the check itself, a re-sync after an upgrade): it says nothing to anyone,
/// so it neither starts a check nor says the pip line, which no one would see.
fn in_background() -> bool {
    std::env::var_os("RB_UPDATE_WORKER").is_some() || std::env::var_os(crate::ledger::resync::BACKGROUND).is_some()
}

/// `rb config` is the user's switch: a check it started would run before its own word is written, so turning updates
/// off could still ask PyPI once. It starts none; the next command does, when updates are on.
fn switching(command: Option<&std::ffi::OsStr>) -> bool {
    command.map_or(false, |c| c == "config")
}

/// As a command starts: when a day has passed since the last check, start one in the background. It never delays the
/// command, and nothing it finds changes this command.
pub fn start_check() {
    if in_background() || switching(std::env::args_os().nth(1).as_deref()) || !running_here(&read()) {
        return;
    }
    let claimed = locked(|| {
        let mut s = read();
        let at = now();
        if !running_here(&s) || at - s.checked_at < DAY {
            return false;
        }
        s.checked_at = at; // claimed before it starts, so commands together start one
        write(&s)
    });
    if claimed != Some(true) {
        return;
    }
    if let Ok(exe) = std::env::current_exe() {
        crate::sys::spawn_detached(&exe, &[WORKER], &[("RB_UPDATE_WORKER", "1")]);
    }
}

/// As a command ends: for rb installed with pip, the line that a newer rb is out, on standard error, at most once a
/// day. A tool install says nothing: it updates itself.
pub fn say_after() {
    if in_background() || !running_here(&read()) {
        return;
    }
    let Ok(exe) = std::env::current_exe() else { return };
    if install_of(&exe) != Install::Pip {
        return;
    }
    let line = locked(|| {
        let mut s = read();
        let at = now();
        let line = pip_line(&s, crate::version(), at)?;
        s.said_at = at;
        write(&s).then_some(line)
    });
    if let Some(Some(line)) = line {
        crate::cli::eprintln_err(&line);
    }
}

/// The newest release PyPI lists, or None when it cannot be read. The request carries nothing about the user.
fn latest_release() -> Option<String> {
    let url = std::env::var("RB_TEST_PYPI").ok().filter(|u| !u.is_empty() && std::env::var_os("RB_TEST_UPDATES").is_some()).unwrap_or_else(|| INDEX.to_string());
    let reply = crate::http::request("GET", &url, &[("accept", "application/json".to_string())], None, Duration::from_secs(10)).ok()?;
    if !(200..300).contains(&reply.status) {
        return None;
    }
    let Ok(v) = pyjson::loads(&String::from_utf8_lossy(&reply.body)) else { return None };
    v.get("info").and_then(|i| i.get("version")).and_then(Value::as_str).map(str::to_string)
}

/// The version the rb at `program` says it is (`rabbit-brain 0.7.0`), asked with nothing that would start another check.
fn says_version(program: &Path) -> Option<String> {
    use std::process::{Command, Stdio};
    let out = Command::new(program).arg("version").env("RB_UPDATE_WORKER", "1").stdin(Stdio::null()).stderr(Stdio::null()).output().ok()?;
    let text = String::from_utf8_lossy(&out.stdout);
    let v = text.trim().strip_prefix("rabbit-brain ")?.trim().to_string();
    (out.status.success() && !v.is_empty()).then_some(v)
}

/// The installer's upgrade for a tool install, run to its end. True when it went through.
fn upgrade(install: Install) -> bool {
    let (program, args): (&str, &[&str]) = match install {
        Install::Uv => ("uv", &["tool", "upgrade", "rabbit-brain"]),
        Install::Pipx => ("pipx", &["upgrade", "rabbit-brain"]),
        Install::Pip => return false,
    };
    let Some(program) = installer(program) else { return false };
    use std::process::{Command, Stdio};
    Command::new(program)
        .args(args)
        .current_dir(crate::sys::neutral_dir())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map_or(false, |st| st.success())
}

/// The background check (`rb __update`): ask PyPI, and for a tool install upgrade through its installer when a newer
/// release is out. Silent whatever happens; its exit code is 0.
pub fn work() -> i32 {
    let s = read();
    if !running_here(&s) {
        return 0;
    }
    let Some(latest) = latest_release() else { return 0 };
    locked(|| {
        let mut s = read();
        s.latest = Some(latest.clone());
        write(&s);
    });
    if !newer(&latest, crate::version()) {
        return 0;
    }
    let Ok(exe) = std::env::current_exe() else { return 0 };
    let Some((install, env)) = tool_env(&exe) else { return 0 };
    // turned off while PyPI answered: the user's word holds, right up to the upgrade
    if !running_here(&read()) || !upgrade(install) {
        return 0;
    }
    // what the installer put in place, as that rb says it: an upgrade can go through and install nothing newer (a
    // pinned tool, an index that lags)
    let program = ["bin", "Scripts"].iter().map(|b| env.join(b).join(exe.file_name().unwrap_or_default())).find(|p| p.is_file());
    let Some(now) = program.as_deref().and_then(says_version).filter(|v| newer(v, crate::version())) else { return 0 };
    locked(|| {
        let mut s = read();
        s.installed = Some(now);
        s.installed_in = Some(crate::sys::slashed(&env));
        write(&s);
    });
    0
}

/// `rb config auto-update [on|off]`: the user's switch, kept in rb's settings folder. With no value it says the
/// setting.
pub fn cmd_config(ns: &crate::argparse::Namespace, out: &mut crate::cli::Out) -> crate::errors::Res<i32> {
    let mut s = read();
    if crate::cli::ns_str(ns, "value").as_deref() == Some("on") && agent_session() {
        // as rb telemetry enable: turning it on is the user's; anyone may turn it off
        return Err(crate::errors::RBError::msg_fix(
            "E_USAGE",
            "rb config auto-update on lets rb check for a newer rb and update itself on this machine, and this is an agent session.",
            "The user runs it in their own terminal. rb config auto-update off works anywhere.",
        ));
    }
    match crate::cli::ns_str(ns, "value").as_deref() {
        Some("on") => s.auto_update = true,
        Some("off") => s.auto_update = false,
        _ => {}
    }
    let value = crate::cli::ns_str(ns, "value");
    let written = value.is_none()
        || locked(|| {
            let mut now = read();
            now.unreadable = false;
            now.auto_update = s.auto_update;
            let ok = write(&now);
            s = now;
            ok
        }) == Some(true);
    if !written {
        return Err(crate::errors::RBError::msg_fix(
            "E_WRITE_FAILED",
            "rb's update settings could not be written.",
            "Use a writable user settings directory outside .rb (RB_CONFIG_DIR), then try again.",
        ));
    }
    let install = std::env::current_exe().map(|e| install_of(&e)).unwrap_or(Install::Pip);
    let how = match install {
        Install::Uv => "rb was installed with uv tool install, so it updates itself, at most once a day",
        Install::Pipx => "rb was installed with pipx, so it updates itself, at most once a day",
        Install::Pip => "rb was not installed as a tool (uv tool install, pipx), so it says when a newer rb is out, at most once a day, and changes nothing",
    };
    let reason = if s.unreadable { Some("unreadable") } else { off_reason(|k| std::env::var(k).ok(), s.auto_update, agent_session()) };
    out.data.insert("auto_update".into(), Value::Bool(s.auto_update));
    out.data.insert("install".into(), Value::Str(match install { Install::Uv => "uv", Install::Pipx => "pipx", Install::Pip => "pip" }.into()));
    out.data.insert("off_because".into(), reason.map_or(Value::Null, |r| Value::Str(r.into())));
    if s.auto_update {
        out.say(format!("Automatic updates are on: {how}."));
        if let Some(r) = reason {
            out.say(match r {
                "do_not_track" => "DO_NOT_TRACK is set, so none runs here.",
                "ci" => "This is a CI environment, so none runs here.",
                "agent_session" => "This is an agent session, so none runs here: its commands keep one rb.",
                "unreadable" => "rb's update settings could not be read, so none runs until rb config auto-update on or off writes them again.",
                _ => "None runs here.",
            });
        }
        out.say("rb config auto-update off turns them off.");
    } else {
        out.say("Automatic updates are off: rb neither checks for a newer rb nor updates itself. rb config auto-update on turns them on.");
    }
    Ok(crate::errors::EXIT_OK)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn versions_compare_by_their_release_numbers_and_a_pre_release_is_never_offered() {
        assert!(newer("0.7.0", "0.6.0"));
        assert!(newer("0.6.10", "0.6.9"));
        assert!(newer("1.0", "0.9.9"));
        assert!(!newer("0.6.0", "0.6.0"));
        assert!(!newer("0.6.0", "0.7.0"));
        assert!(!newer("0.7.0rc1", "0.6.0"), "a pre-release is never offered");
        assert!(newer("0.7.0", "0.7.0rc1"), "a release is newer than its pre-release");
        assert!(!newer("0.6.0", "0.6.0+local"));
        assert!(!newer("0.6.0", "0.6.0.post1"), "a post-release is not older than its release");
        assert!(newer("0.6.1", "0.6.0.post1"));
        assert!(newer("0.6.0", "0.6.0.dev1"));
        assert!(!newer("0.7", "0.7.0"));
        assert!(!newer("0.7.0.dev1", "0.6.0"));
    }

    #[test]
    fn updates_are_off_in_ci_agent_sessions_the_tests_do_not_track_and_by_the_users_setting() {
        let none = |_: &str| None;
        assert_eq!(off_reason(none, true, false), None);
        assert_eq!(off_reason(none, false, false), Some("config"));
        assert_eq!(off_reason(none, true, true), Some("agent_session"));
        assert_eq!(off_reason(|k| (k == "DO_NOT_TRACK").then(|| "1".into()), true, false), Some("do_not_track"));
        assert_eq!(off_reason(|k| (k == "DO_NOT_TRACK").then(|| "0".into()), true, false), None);
        assert_eq!(off_reason(|k| (k == "GITHUB_ACTIONS").then(|| "true".into()), true, false), Some("ci"));
        assert_eq!(off_reason(|k| (k == "PYTEST_CURRENT_TEST").then(|| "t".into()), true, false), Some("test"));
        assert_eq!(off_reason(|k| (k == "RB_AUTO_UPDATE").then(|| "0".into()), true, false), Some("config"));
        assert_eq!(off_reason(|k| (k == "RB_AUTO_UPDATE").then(|| "1".into()), true, false), None);
    }

    #[test]
    fn the_pip_line_says_a_newer_release_at_most_once_a_day() {
        let s = State { auto_update: true, latest: Some("0.7.0".into()), said_at: 1_000, ..State::default() };
        assert_eq!(pip_line(&s, "0.6.0", 1_000 + DAY).as_deref(), Some("rb 0.7.0 is out: pip install -U rabbit-brain"));
        assert_eq!(pip_line(&s, "0.6.0", 1_000 + DAY - 1), None, "said less than a day ago");
        assert_eq!(pip_line(&s, "0.7.0", 1_000 + DAY), None, "this rb is the newest");
        assert_eq!(pip_line(&State::default(), "0.6.0", DAY * 9), None, "no check found one");
    }

    #[test]
    fn a_tool_install_is_told_by_its_installers_mark_in_its_environment() {
        let root = std::env::temp_dir().join(format!("rb-updates-{}-{:08x}", std::process::id(), crate::rng::os_randbits(32)));
        let env = root.join("uv/tools/rabbit-brain");
        std::fs::create_dir_all(env.join("bin")).unwrap();
        std::fs::write(env.join("bin/rb"), b"").unwrap();
        std::fs::write(env.join("pyvenv.cfg"), b"home = /usr/bin\n").unwrap();
        assert_eq!(install_of(&env.join("bin/rb")), Install::Pip, "no mark");
        std::fs::write(env.join("uv-receipt.toml"), b"[tool]\nrequirements = [{ name = \"other\" }]\n").unwrap();
        assert_eq!(install_of(&env.join("bin/rb")), Install::Pip, "a receipt for another tool");
        std::fs::write(env.join("uv-receipt.toml"), b"[tool]\nrequirements = [{ name = \"rabbit-brain\" }]\n").unwrap();
        assert_eq!(install_of(&env.join("bin/rb")), Install::Uv);
        let pipx = root.join("pipx/venvs/rabbit-brain");
        std::fs::create_dir_all(pipx.join("bin")).unwrap();
        std::fs::write(pipx.join("bin/rb"), b"").unwrap();
        std::fs::write(pipx.join("pyvenv.cfg"), b"home = /usr/bin\n").unwrap();
        std::fs::write(pipx.join("pipx_metadata.json"), b"{\"main_package\": {\"package\": \"rabbit-brain\"}}").unwrap();
        assert_eq!(install_of(&pipx.join("bin/rb")), Install::Pipx);
        // a project that holds a file named like uv's receipt, above the environment it made: no tool
        let project = root.join("project");
        std::fs::create_dir_all(project.join(".venv/bin")).unwrap();
        std::fs::write(project.join(".venv/bin/rb"), b"").unwrap();
        std::fs::write(project.join(".venv/pyvenv.cfg"), b"home = /usr/bin\n").unwrap();
        std::fs::write(project.join("uv-receipt.toml"), b"rabbit-brain").unwrap();
        std::fs::write(project.join(".venv/uv-receipt.toml"), b"rabbit-brain").unwrap();
        assert_eq!(install_of(&project.join(".venv/bin/rb")), Install::Pip);
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn the_installer_is_never_one_a_checkout_puts_on_path() {
        let root = std::env::temp_dir().join(format!("rb-installer-{}-{:08x}", std::process::id(), crate::rng::os_randbits(32)));
        let file = if cfg!(windows) { "rb-test-installer.exe" } else { "rb-test-installer" };
        let repo_bin = root.join("repo/bin");
        std::fs::create_dir_all(&repo_bin).unwrap();
        std::fs::create_dir_all(root.join("repo/.git")).unwrap();
        std::fs::write(repo_bin.join(file), b"").unwrap();
        let own_bin = root.join("home/bin");
        std::fs::create_dir_all(&own_bin).unwrap();
        let path = std::env::join_paths([repo_bin.clone(), PathBuf::from("relative/bin"), own_bin.clone()]).unwrap();
        assert_eq!(installer_on("rb-test-installer", &path), None, "only a checkout's folder holds it");
        std::fs::write(own_bin.join(file), b"").unwrap();
        assert_eq!(installer_on("rb-test-installer", &path), Some(own_bin.join(file)));
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn rb_config_starts_no_check_and_every_other_command_may() {
        use std::ffi::OsStr;
        assert!(switching(Some(OsStr::new("config"))));
        assert!(!switching(Some(OsStr::new("version"))) && !switching(Some(OsStr::new("status"))) && !switching(None));
    }

    #[test]
    fn the_lock_lets_one_step_through_at_a_time() {
        let lock = std::env::temp_dir().join(format!("rb-lock-{}-{:08x}.lock", std::process::id(), crate::rng::os_randbits(32)));
        let inner = hold(&lock, || hold(&lock, || 1));
        assert_eq!(inner, Some(None), "a second step waits, then gives up while the first holds it");
        assert_eq!(hold(&lock, || 2), Some(2), "released after the step");
    }
}
