//! The report link (notes/design/final/REPORT-LINK.md): one readable address that hands the hub the circumstances of
//! a fault in rb, or a word of feedback, for the user to look at and send. The query string is the whole report, and
//! it holds only what this file puts there: rb's version, the platform, the command's path in rb's own command tree,
//! and for a fault its place in rb's own source and a fingerprint of its message. Never an argument, a path on the
//! machine, the fault's message itself or anything from the record. rb sends nothing: the user opens the page.

use crate::errors::RBError;
use crate::value::Value;
use std::cell::RefCell;

/// What a fault said and where it happened, noted as it happens (the panic hook, a fault rb built) for the failure
/// that says it.
#[derive(Clone, Debug, PartialEq)]
pub struct Fault {
    /// The fault's own message, which never leaves the machine: only its fingerprint does.
    pub what: String,
    /// The fault's place as the link says it: `rust/src/<file>.rs:<line>`, or `dep` inside a dependency.
    pub at: Option<String>,
}

thread_local! {
    static NOTED: RefCell<Option<Fault>> = const { RefCell::new(None) };
}

/// Note a fault as it happens: its message and the place the compiler names for it (a file and a line).
pub fn note(what: &str, file: Option<&str>, line: u32) {
    let at = file.map(|f| at_of(f, line));
    NOTED.with(|n| *n.borrow_mut() = Some(Fault { what: what.to_string(), at }));
}

/// The fault noted last on this thread, taken.
pub fn take() -> Option<Fault> {
    NOTED.with(|n| n.borrow_mut().take())
}

/// A place in rb's own source as the link says it, or `dep`. The compiler names rb's own files from its crate
/// (`src/cli.rs`, or `rust/src/cli.rs` from the workspace above it), and a dependency's or the standard library's by a
/// path of their own (`/home/ana/.cargo/registry/...`, `/rustc/<hash>/library/...`): those, and anything that is not a
/// plain path to a `.rs` file under `src/`, are `dep`, so no path of the machine's is ever said.
pub fn at_of(file: &str, line: u32) -> String {
    let file = file.replace('\\', "/");
    let own = file.strip_prefix("rust/src/").or_else(|| file.strip_prefix("src/"));
    let place = own.filter(|rest| {
        rest.ends_with(".rs")
            && rest.split('/').all(|part| !part.is_empty() && part != "." && part != "..")
            && rest.chars().all(|c| c.is_ascii_alphanumeric() || "_./-".contains(c))
    });
    match place {
        Some(rest) if line > 0 && line <= 999_999 && rest.len() + "rust/src/".len() <= 96 => format!("rust/src/{rest}:{line}"),
        _ => "dep".to_string(),
    }
}

/// The first 8 hex digits of the SHA-256 of a fault's message.
pub fn fingerprint(what: &str) -> String {
    crate::sources::sha256_bytes(what.as_bytes()).chars().take(8).collect()
}

/// rb's version as the link carries it: `[0-9A-Za-z.+-]{1,32}`.
fn version() -> String {
    let v: String = crate::version().chars().filter(|c| c.is_ascii_alphanumeric() || ".+-".contains(*c)).take(32).collect();
    if v.is_empty() { "unknown".to_string() } else { v }
}

/// The platform as Rust names it, `<os>-<arch>`: `linux-x86_64`, `macos-aarch64`.
pub fn platform() -> String {
    let p: String = format!("{}-{}", std::env::consts::OS, std::env::consts::ARCH)
        .chars()
        .filter(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || "_-".contains(*c))
        .take(32)
        .collect();
    if p.is_empty() { "unknown".to_string() } else { p }
}

/// The command's path in rb's own command tree, with dots (`run.add`), from the words it was run with: the longest
/// run of leading words the tree has, so an argument or an option after them never goes in. None when the first word
/// is no rb command.
pub fn command_path(words: &str) -> Option<String> {
    let mut parser = crate::argparse::rb_parser();
    let mut path: Vec<String> = Vec::new();
    for word in words.split_whitespace() {
        if !parser.subcommands().iter().any(|(name, _)| name == word) {
            break;
        }
        path.push(word.to_string());
        let as_strs: Vec<&str> = path.iter().map(String::as_str).collect();
        match crate::argparse::rb_parser().sub(&as_strs) {
            Some(p) => parser = p,
            None => break,
        }
    }
    let c = path.join(".");
    let fits = !c.is_empty() && c.len() <= 48 && c.chars().all(|ch| ch.is_ascii_lowercase() || ch.is_ascii_digit() || ".-".contains(ch));
    fits.then_some(c)
}

/// The hub rb is configured for: RB_HUB_URL when it names a web address (without the credentials, query or fragment
/// it may carry), else the built-in default.
pub fn hub() -> String {
    let named = crate::hub::hub_of(None).trim().to_string();
    let base = if crate::http::is_address(&named) && !named.contains(char::is_whitespace) { crate::http::shareable(&named) } else { crate::hub::DEFAULT_HUB.to_string() };
    base.trim_end_matches('/').to_string()
}

/// A value as the query carries it: what the patterns allow stays as it is, but `+`, which a query reads as a space.
fn enc(v: &str) -> String {
    v.chars()
        .map(|c| if c.is_ascii_alphanumeric() || "-._~/:".contains(c) { c.to_string() } else { format!("%{:02X}", c as u32) })
        .collect()
}

fn link(pairs: &[(&str, Option<String>)]) -> String {
    let query: Vec<String> = pairs.iter().filter_map(|(k, v)| v.as_ref().map(|v| format!("{k}={}", enc(v)))).collect();
    format!("{}/report?{}", hub(), query.join("&"))
}

/// The link for a fault in rb: the command it stopped (its words, as rb ran it), and the fault noted for it.
pub fn crash_link(command: &str, fault: &Fault) -> String {
    link(&[
        ("k", Some("crash".into())),
        ("v", Some(version())),
        ("os", Some(platform())),
        ("c", command_path(command)),
        ("at", fault.at.clone()),
        ("h", Some(fingerprint(&fault.what))),
    ])
}

/// The link for a word of feedback, about a command when one is named.
pub fn feedback_link(about: Option<&str>) -> String {
    link(&[("k", Some("feedback".into())), ("v", Some(version())), ("os", Some(platform())), ("c", about.and_then(command_path))])
}

/// The sentence a fault's fix line says, with the link.
pub fn fix_line(url: &str) -> String {
    format!("This is a fault in rb. Report it (the page shows what it sends before you send it): {url}")
}

/// Whether a failure is a fault in rb: an E_INTERNAL that tells the user nothing of their own to do. One that names
/// an install to make (the screen's Python, a reinstall) keeps its fix.
pub fn is_fault(err: &RBError) -> bool {
    err.code == "E_INTERNAL" && (err.message.starts_with("rb itself failed") || err.fix == crate::errors::meaning("E_INTERNAL").map_or("", |m| m.1))
}

/// A failure as it is said to the user: a fault gets the report link as its fix, and `report_url` beside it under
/// --json. The place and the fingerprint are the noted fault's when it is this one; otherwise the fingerprint is of
/// the failure's own message, and no place is said.
pub fn reported(mut err: RBError, command: &str) -> RBError {
    if !is_fault(&err) {
        return err;
    }
    // a fault noted for another failure (one a caller let go) is not this one's
    let fault = take().filter(|f| err.message.contains(&f.what)).unwrap_or_else(|| Fault { what: err.message.clone(), at: None });
    let url = crash_link(command, &fault);
    err.fix = fix_line(&url);
    err.with_extra("report_url", Value::Str(url))
}

/// Whether rb feedback opens the page itself: in the user's own interactive terminal (standard input and output a
/// terminal), outside an agent session, and not under --json. Anywhere else it only prints the link.
pub fn opens_browser(terminal: bool, agent_session: bool, json: bool) -> bool {
    terminal && !agent_session && !json
}

/// Whether a browser can show a page here: on Linux and the BSDs only with a display (X or Wayland); without one,
/// xdg-open would start a text browser with no terminal, or fail.
pub fn has_display(get: impl Fn(&str) -> Option<String>) -> bool {
    if cfg!(any(target_os = "macos", windows)) {
        return true;
    }
    ["DISPLAY", "WAYLAND_DISPLAY"].iter().any(|k| get(k).map_or(false, |v| !v.is_empty()))
}

/// Open an address in the user's browser. The opener hands the page to the browser and ends: one that ends failing in
/// its first seconds opened nothing; one still running then is taken to have opened it.
pub fn open_in_browser(url: &str) -> bool {
    use std::process::{Command, Stdio};
    let mut c = if cfg!(target_os = "macos") {
        let mut c = Command::new("open");
        c.arg(url);
        c
    } else if cfg!(windows) {
        let mut c = Command::new("rundll32");
        c.args(["url.dll,FileProtocolHandler", url]);
        c
    } else {
        let mut c = Command::new("xdg-open");
        c.arg(url);
        c
    };
    let Ok(mut child) = c.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn() else { return false };
    let until = std::time::Instant::now() + std::time::Duration::from_secs(3);
    while std::time::Instant::now() < until {
        match child.try_wait() {
            Ok(Some(status)) => return status.success(),
            Ok(None) => std::thread::sleep(std::time::Duration::from_millis(50)),
            Err(_) => return false,
        }
    }
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    fn keys_match(url: &str) {
        let (_, query) = url.split_once("/report?").expect("a report address");
        for pair in query.split('&') {
            let (k, v) = pair.split_once('=').expect("key=value");
            let v = v.replace("%2B", "+");
            let ok = match k {
                "k" => v == "crash" || v == "feedback",
                "v" => (1..=32).contains(&v.len()) && v.chars().all(|c| c.is_ascii_alphanumeric() || ".+-".contains(c)),
                "os" => (1..=32).contains(&v.len()) && v.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || "_-".contains(c)),
                "c" => (1..=48).contains(&v.len()) && v.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || ".-".contains(c)),
                "at" => v == "dep" || v.rsplit_once(':').map_or(false, |(p, l)| {
                    (1..=96).contains(&p.len()) && p.chars().all(|c| c.is_ascii_alphanumeric() || "_./-".contains(c))
                        && (1..=6).contains(&l.len()) && l.chars().all(|c| c.is_ascii_digit())
                }),
                "h" => v.len() == 8 && v.chars().all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()),
                _ => false,
            };
            assert!(ok, "{k}={v} in {url}");
        }
    }

    #[test]
    fn a_place_in_rbs_own_source_is_said_from_rust_src_and_anything_else_is_dep() {
        assert_eq!(at_of("src/cli.rs", 653), "rust/src/cli.rs:653");
        assert_eq!(at_of("rust/src/ledger/views.rs", 12), "rust/src/ledger/views.rs:12");
        assert_eq!(at_of("src\\cmds\\record.rs", 9), "rust/src/cmds/record.rs:9");
        assert_eq!(at_of("/home/ana/.cargo/registry/src/index.crates.io-6f17d22bba15001f/indexmap-2.2.6/src/map.rs", 40), "dep");
        assert_eq!(at_of("/rustc/90b35a6239c3d8bdabc530a6a0816f7ff89a0aaf/library/core/src/str/mod.rs", 1), "dep");
        assert_eq!(at_of("/srv/rb/x-m/src/rust/src/cli.rs", 3), "dep", "an absolute path is never said");
        assert_eq!(at_of("src/../../etc/x.rs", 3), "dep");
        assert_eq!(at_of("src/cli.rs", 0), "dep");
    }

    #[test]
    fn a_crash_link_holds_only_the_specs_keys_and_none_of_the_message() {
        let what = "the decision dab12cd just written has no authority, in /srv/rb/ana/int8-resnet50/.rb";
        let url = crash_link("run add", &Fault { what: what.into(), at: Some(at_of("src/cmds/record.rs", 2252)) });
        keys_match(&url);
        assert!(url.contains("k=crash") && url.contains("c=run.add") && url.contains("at=rust/src/cmds/record.rs:2252"), "{url}");
        assert!(url.contains(&format!("h={}", fingerprint(what))), "{url}");
        for part in ["dab12cd", "authority", "/srv", "int8", ".rb"] {
            assert!(!url.split_once('?').unwrap().1.contains(part), "{part} in {url}");
        }
    }

    #[test]
    fn the_command_is_said_from_rbs_command_tree_and_never_its_arguments() {
        assert_eq!(command_path("run add --from out/seed1/metrics.json").as_deref(), Some("run.add"));
        assert_eq!(command_path("env check").as_deref(), Some("env.check"));
        assert_eq!(command_path("status int8").as_deref(), Some("status"));
        assert_eq!(command_path("sync").as_deref(), Some("sync"));
        assert_eq!(command_path("/srv/rb/ana"), None);
        assert_eq!(command_path(""), None);
        let url = feedback_link(Some("show c1 --json"));
        keys_match(&url);
        assert!(url.contains("k=feedback") && url.contains("c=show") && !url.contains("c1"), "{url}");
        assert!(!feedback_link(Some("nonsense words")).contains("c="));
    }

    #[test]
    fn a_plus_in_the_version_is_sent_as_itself() {
        assert_eq!(enc("0.6.0+local"), "0.6.0%2Blocal");
        assert_eq!(enc("rust/src/cli.rs:3"), "rust/src/cli.rs:3");
    }

    #[test]
    fn a_browser_needs_a_display_where_xdg_open_would_find_none() {
        if cfg!(any(target_os = "macos", windows)) {
            return;
        }
        assert!(!has_display(|_| None));
        assert!(!has_display(|k| (k == "DISPLAY").then(String::new)));
        assert!(has_display(|k| (k == "DISPLAY").then(|| ":0".into())));
        assert!(has_display(|k| (k == "WAYLAND_DISPLAY").then(|| "wayland-0".into())));
    }

    #[test]
    fn a_fault_noted_for_another_failure_is_not_this_ones() {
        note("an earlier fault", Some("src/telemetry.rs"), 9);
        let err = reported(RBError::fault("the bundle it wrote has no page.html"), "sync");
        let url = err.extra.get("report_url").and_then(Value::as_str).unwrap().to_string();
        assert!(!url.contains("at=") && url.contains(&format!("h={}", fingerprint(&err.message))), "{url}");
    }

    #[test]
    fn only_the_users_own_terminal_opens_the_page() {
        assert!(opens_browser(true, false, false));
        assert!(!opens_browser(true, true, false), "an agent session only prints it");
        assert!(!opens_browser(false, false, false), "no terminal");
        assert!(!opens_browser(true, false, true), "--json");
    }

    #[test]
    fn a_fault_gets_the_link_and_an_install_to_make_keeps_its_fix() {
        let _ = take();
        let fault = reported(RBError::fault("this rb has no schema called nope"), "schema");
        assert!(fault.fix.starts_with("This is a fault in rb. Report it (the page shows what it sends before you send it): "), "{}", fault.fix);
        let url = fault.extra.get("report_url").and_then(Value::as_str).expect("report_url").to_string();
        assert!(fault.fix.ends_with(&url));
        keys_match(&url);
        assert!(!url.contains("at="), "a fault rb built names no place it was not given");
        let install = RBError::msg_fix("E_INTERNAL", "the screen is drawn by rb's Python package, and it is not installed.", "Install it: pip install rabbit-brain.");
        let kept = reported(install, "ui");
        assert_eq!(kept.fix, "Install it: pip install rabbit-brain.");
        assert!(!kept.extra.contains_key("report_url"));
        let usage = reported(RBError::msg("E_USAGE", "no"), "status");
        assert!(!usage.extra.contains_key("report_url"));
    }
}
