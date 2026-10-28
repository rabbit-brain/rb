//! Updates without the user, layer 2 (SPRINT-OCT24.md, "Updates without the user"): once rb is upgraded, a project
//! already set up to sync re-syncs once on its own, so the copy in the user's account shows what the new rb computes.
//! A sync that went through keeps its state in `.rb/cache/sync.json` (local, gitignored): the hub it went to, whether
//! the record went with the page, and the version of rb that wrote the page. The first command a newer rb runs here
//! starts one `rb sync` in the background, the way telemetry's delivery runs, and never delays the command or changes
//! its result. A sync that fails then is not tried again until the next upgrade.

use crate::pyjson;
use crate::value::{Dict, Value};
use std::path::{Path, PathBuf};

/// Set on the background sync rb starts, which is no command a user ran.
pub const BACKGROUND: &str = "RB_BACKGROUND_SYNC";

/// What the last sync from this checkout was.
#[derive(Clone, Debug, PartialEq)]
pub struct Synced {
    pub hub: String,
    pub with_record: bool,
    /// The version of rb that wrote the page it sent.
    pub version: String,
}

fn state_path(root: &Path) -> PathBuf {
    root.join(".rb").join("cache").join("sync.json")
}

/// The last sync from the record at `root`, when one went through.
pub fn synced(root: &Path) -> Option<Synced> {
    let text = std::fs::read_to_string(state_path(root)).ok()?;
    let Ok(Value::Dict(d)) = pyjson::loads(&text) else { return None };
    let hub = d.get("hub").and_then(Value::as_str).filter(|h| !h.is_empty())?.to_string();
    let version = d.get("version").and_then(Value::as_str).unwrap_or("").to_string();
    let with_record = matches!(d.get("with_record"), Some(Value::Bool(true)));
    Some(Synced { hub, with_record, version })
}

fn write(root: &Path, s: &Synced) -> bool {
    let path = state_path(root);
    if let Some(dir) = path.parent() {
        if std::fs::create_dir_all(dir).is_err() {
            return false;
        }
    }
    let mut d = Dict::new();
    d.insert("hub".into(), Value::Str(s.hub.clone()));
    d.insert("with_record".into(), Value::Bool(s.with_record));
    d.insert("version".into(), Value::Str(s.version.clone()));
    d.insert("at".into(), Value::Str(crate::pytime::now_dt().to_rfc3339()));
    let text = pyjson::dumps_with(&Value::Dict(d), &pyjson::Dumps::indent(1)).unwrap_or_default();
    crate::ledger::write_atomic(&path, text.as_bytes()).is_ok()
}

/// The hub a sync went to, as rb sync names it: --hub, or RB_HUB_URL, or rabbitbrain.ai, without a trailing slash.
fn hub_named(flag: Option<String>) -> String {
    crate::hub::hub_of(flag).trim_end_matches('/').to_string()
}

/// Whether a command, by its words, leaves the background sync alone: a sync of its own, the hub's own commands, the
/// commands that start or take away a record, and the ones that hold the terminal or a connection open.
fn leaves_it(command: &str) -> bool {
    let first = command.split_whitespace().next().unwrap_or("");
    matches!(first, "" | "sync" | "login" | "logout" | "init" | "clone" | "remove" | "telemetry" | "mcp" | "ui" | "open" | "tour" | "feedback")
}

/// After a command: a sync that went through keeps its state; any other command, run by a newer rb than the one that
/// wrote the last synced page, starts the one re-sync. Nothing here can fail the command.
pub fn after_command(command: &str, code: i32, hub_flag: Option<String>, with_record: bool, watch: bool) {
    if std::env::var_os(BACKGROUND).is_some() {
        // the background sync itself keeps its state as any sync does, and starts none
        if command == "sync" && code == crate::errors::EXIT_OK {
            if let Some(root) = crate::ledger::find_root(None) {
                write(&root, &Synced { hub: hub_named(hub_flag), with_record, version: crate::version().to_string() });
            }
        }
        return;
    }
    let Some(root) = crate::ledger::find_root(None) else { return };
    if command == "sync" {
        if code == crate::errors::EXIT_OK && !watch {
            write(&root, &Synced { hub: hub_named(hub_flag), with_record, version: crate::version().to_string() });
        }
        return;
    }
    // a CI job, and an agent's session (its commands keep one rb): the user's next command re-syncs instead
    if leaves_it(command) || crate::ledger::updates::ci() || crate::ledger::updates::agent_session() {
        return;
    }
    let _ = resync_if_upgraded(&root);
}

/// The one re-sync after an upgrade, started in the background: true when it was started. The state takes the new
/// version first, so commands that follow (or run beside this one) start no second one.
pub fn resync_if_upgraded(root: &Path) -> bool {
    if synced(root).is_none() {
        return false; // most commands, in a project never synced, stop here and take no lock
    }
    let now = crate::version().to_string();
    // read, checked and claimed under one lock, so commands together start one re-sync
    let claimed = crate::ledger::updates::hold(&state_path(root).with_extension("lock"), || {
        let last = synced(root)?;
        // an upgrade only: the same rb, an older one (a downgrade, or another install of rb on this machine) sends nothing
        if !crate::ledger::updates::newer(&now, &last.version) {
            return None;
        }
        write(root, &Synced { version: now.clone(), ..last.clone() }).then_some(last)
    });
    let Some(Some(last)) = claimed else { return false };
    let Ok(exe) = std::env::current_exe() else { return false };
    let mut args = vec!["sync".to_string(), "--hub".to_string(), last.hub.clone()];
    if last.with_record {
        args.push("--with-record".into());
    }
    spawn_in(&exe, root, &args)
}

/// Start rb in the record's folder, on its own: no terminal, none of this command's open files (a caller waiting on
/// a pipe this command holds sees it close when this command ends), nothing it says reaches this command's output,
/// and it outlives this command.
fn spawn_in(exe: &Path, root: &Path, args: &[String]) -> bool {
    use std::process::{Command, Stdio};
    let mut c = Command::new(exe);
    c.args(args).current_dir(root).env(BACKGROUND, "1").stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        c.process_group(0);
        // worked out before the fork: between fork and exec only async-signal-safe calls (close) are made
        let end = match unsafe { libc::sysconf(libc::_SC_OPEN_MAX) } {
            n if n > 3 && n <= 65_536 => n as libc::c_int,
            _ => 4096,
        };
        unsafe {
            c.pre_exec(move || {
                for fd in 3..end {
                    libc::close(fd);
                }
                Ok(())
            });
        }
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // no console of its own and none shared: DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        c.creation_flags(0x0000_0008 | 0x0000_0200);
    }
    match c.spawn() {
        Ok(child) => {
            drop(child); // never waited for: it outlives this command
            true
        }
        Err(_) => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch() -> PathBuf {
        let root = std::env::temp_dir().join(format!("rb-resync-{}-{:08x}", std::process::id(), crate::rng::os_randbits(32)));
        std::fs::create_dir_all(root.join(".rb")).unwrap();
        root
    }

    #[test]
    fn a_sync_state_reads_back_as_written() {
        let root = scratch();
        assert_eq!(synced(&root), None, "no sync went through here");
        let s = Synced { hub: "https://hub.example.org".into(), with_record: true, version: "0.6.0".into() };
        assert!(write(&root, &s));
        assert_eq!(synced(&root), Some(s));
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn the_same_rb_starts_no_resync_and_a_record_never_synced_starts_none() {
        let root = scratch();
        assert!(!resync_if_upgraded(&root));
        write(&root, &Synced { hub: "https://hub.example.org".into(), with_record: false, version: crate::version().to_string() });
        assert!(!resync_if_upgraded(&root));
        write(&root, &Synced { hub: "https://hub.example.org".into(), with_record: false, version: "999.0.0".into() });
        assert!(!resync_if_upgraded(&root), "an older rb than the one that wrote the page sends nothing");
        assert_eq!(synced(&root).unwrap().version, "999.0.0");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn the_commands_that_leave_the_resync_alone() {
        for c in ["sync", "login", "logout", "init", "clone", "remove", "telemetry flush", "mcp", "ui", "open", ""] {
            assert!(leaves_it(c), "{c}");
        }
        for c in ["status", "run add", "context", "show"] {
            assert!(!leaves_it(c), "{c}");
        }
    }
}
