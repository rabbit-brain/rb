//! `rb export`: the project's page, readable without rb, and with --with-record the record too, as one portable bundle.

use crate::errors::{RBError, Res};
use crate::ledger::{Ledger, DIR, KEPT_MERGES, UNSHALLOW, UNSHALLOW_CI};
use crate::pyjson::{self, py_str};
use crate::record::*;
use crate::value::{Dict, DictExt, Value};
use std::path::{Path, PathBuf};

pub const FORMAT: &str = "rb-bundle";
/// Where a bundle holds the record: its record/, beside the manifest.
pub const RECORD: &str = "record";
pub const FORMAT_VERSION: i64 = 2;
const SKIP_DIRS: &[&str] = &["cache", "journal", "objects"];
const SKIP_FILES: &[&str] = &[".lock"];
const DEFAULT_DIR: &str = "rb-export";
pub const README: &str = include_str!("assets/export_readme.txt");
/// What a record that goes out keeps of who wrote it and where: rb says it whenever an export or a sync takes it along.
pub const RECORD_CARRIES: &str = "The record keeps who made each write (human:<name> or agent:<name>, and the channel it came through), an agent's chat session, name, version, provider and model, the user's words passed with --words, and each run's host, platform and branch.";
/// RECORD_CARRIES for a record that keeps the path of a linked worktree a write was made in.
const RECORD_CARRIES_WORKTREES: &str = "The record keeps who made each write (human:<name> or agent:<name>, and the channel it came through), an agent's chat session, name, version, provider and model, the user's words passed with --words, each run's host, platform and branch, and the path of each linked worktree a write was made in.";
/// What the README says of a bundle whose record keeps the path of a linked worktree a write was made in.
const WORKTREES: &str = "\nA write made in a linked git worktree keeps that worktree's path, on the machine it was
made on, on its line of log.jsonl and on its receipt. page.html names no path.\n";

/// Whether the record keeps the path of a linked worktree a write was made in (its log line's `worktree`).
fn keeps_worktrees(led: &Ledger) -> bool {
    led.log_entries().map_or(false, |rows| rows.iter().any(|r| r.contains_key("worktree")))
}

/// What a record that goes out keeps, as rb says it whenever an export or a sync takes it along.
pub fn record_carries(led: &Ledger) -> &'static str {
    if keeps_worktrees(led) {
        RECORD_CARRIES_WORKTREES
    } else {
        RECORD_CARRIES
    }
}
/// The README of an export without --with-record: the page alone.
const PAGE_ONLY: &str = "This is an rb export of one project's record of experiments: its page, without the record.

What is here

  page.html       The record rendered for reading: every criterion with its comparison,
                  its rule and the rest of its reading, computed at export time. Open it
                  in any browser.
  manifest.json   page.html and this README with their sha256, plus what wrote this
                  export and when.

The record itself, its YAML files and its log, stays in the repository it was exported
from: rb export --with-record writes a bundle that carries it too.
";
/// What the README says of a bundle that keeps files its checks read at a commit.
const READ_AT_COMMIT: &str = "\nFiles the record's checks read at a git commit are read here as they are at home:
record/.rb/cache/commits/<commit>/<path> holds each as it stood at that commit, and
record/.rb/.gitignore keeps them in git with the bundle. They are rb's copies of those
files, not records (they do not open with rb: 1), and rb reads one only when its sha256
is the one the record holds for the file it verified there.\n";
/// What the README says of a bundle that carries what merged records rest on.
const MERGED: &str = "\nRecords git merged from two branches read here as rb's own, as they do at home:
record/.rb/merges.json holds rb's copies of the versions each was merged from,
and which made which, so rb status checks every such merge again without git,
while manifest.json names that file as it is.\n";

/// A part of the bundle that could not be written.
fn io_err(e: std::io::Error, p: &Path) -> RBError {
    RBError::could_not_write(&e, p)
}

/// The bytes, and the source's times and mode.
fn copy2(src: &Path, dst: &Path) -> Res<()> {
    std::fs::copy(src, dst).map_err(|e| {
        let (from, to) = (crate::sys::slashed(src), crate::sys::slashed(dst));
        RBError::msg_fix("E_WRITE_FAILED", format!("Could not copy {from} to {to}: {}", crate::sys::os_words(&e)), format!("Check that {from} can be read and that {to} can be written."))
    })?;
    crate::sys::copy_times(src, dst);
    Ok(())
}

fn sorted_entries(dir: &Path) -> Vec<std::fs::DirEntry> {
    let mut v: Vec<std::fs::DirEntry> = std::fs::read_dir(dir).map(|rd| rd.flatten().collect()).unwrap_or_default();
    v.sort_by_key(|e| e.file_name());
    v
}

fn copy_tree_files(src: &Path, dest: &Path, top: bool) -> Res<()> {
    let entries = sorted_entries(src);
    for e in entries.iter() {
        let name = e.file_name().to_string_lossy().into_owned();
        let p = e.path();
        let is_dir = e.file_type().map(|t| t.is_dir()).unwrap_or(false);
        let link_to_dir = e.file_type().map(|t| t.is_symlink()).unwrap_or(false) && p.is_dir();
        if is_dir || link_to_dir {
            continue;
        }
        // a bundle's kept merges are its own export's: one exported again keeps what it rests on afresh
        if SKIP_FILES.contains(&name.as_str()) || (top && name == KEPT_MERGES) {
            continue;
        }
        let out = dest.join(&name);
        std::fs::create_dir_all(dest).map_err(|e| io_err(e, dest))?;
        copy2(&p, &out)?;
    }
    for e in entries {
        let name = e.file_name().to_string_lossy().into_owned();
        let is_dir = e.file_type().map(|t| t.is_dir()).unwrap_or(false);
        if !is_dir || (top && SKIP_DIRS.contains(&name.as_str())) {
            continue;
        }
        copy_tree_files(&e.path(), &dest.join(&name), false)?;
    }
    Ok(())
}

/// A bundle has no git to show how a record got there: one whose making this shallow clone cannot show (shallow_clone)
/// would read there as edited outside rb, so the export waits for the whole history.
fn refuse_unseen(led: &Ledger) -> Res<()> {
    let tam = led.tampered()?;
    let unseen: Vec<&Dict> = tam.iter().filter(|t| t.get("shallow").map_or(false, |v| v.truthy())).collect();
    let Some(first) = unseen.first() else { return Ok(()) };
    let more = match unseen.len() - 1 {
        0 => String::new(),
        n => format!(", and {n} more {} like it (rb doctor lists them)", crate::assess::plural("record", n as i64)),
    };
    Err(RBError::msg_fix(
        "E_STATE_EDITED",
        format!("{}; a bundle, which has no git, would read it as edited outside rb{more}.", first.get_str("why").unwrap_or("")),
        format!("{UNSHALLOW} fetches the whole history, so rb can see how it got there; {UNSHALLOW_CI}."),
    ))
}

/// Every source the record's checks read.
fn checked_sources(led: &Ledger) -> Res<Vec<Source>> {
    let mut out: Vec<Source> = Vec::new();
    for c in led.all::<Criterion>()?.iter() {
        if c.retracted.is_none() {
            out.extend(c.source.clone());
        }
    }
    for e in led.all::<Experiment>()?.iter() {
        if e.retracted.is_some() {
            continue;
        }
        for s in e.settings.iter().chain(e.variants.iter().flat_map(|v| v.settings.iter())) {
            if s.retracted.is_none() {
                out.extend(s.source.clone());
                out.extend(s.cited.as_ref().and_then(|c| c.source.clone()));
            }
        }
    }
    for a in led.all::<Analysis>()?.iter() {
        if a.retracted.is_none() {
            out.extend(a.source.clone());
            out.extend(a.comparisons.iter().filter_map(|c| c.source.clone()));
        }
    }
    Ok(out)
}

/// Every repository file the record's checks read (a run source's directory by the record.json in it), and whether one
/// of them reads it as it is now rather than at a commit.
fn source_paths(led: &Ledger) -> Res<Vec<(String, bool)>> {
    let mut paths: std::collections::BTreeMap<String, bool> = std::collections::BTreeMap::new();
    for s in checked_sources(led)? {
        let Some(path) = s.path.clone().filter(|p| !p.is_empty() && (s.kind == "file" || s.kind == "run")) else { continue };
        let path = match s.kind == "run" && led.root.join(&path).is_dir() {
            true if path == "." => "record.json".to_string(),
            true => format!("{path}/record.json"),
            false => path,
        };
        *paths.entry(path).or_default() |= s.commit.as_deref().map_or(true, str::is_empty);
    }
    Ok(paths.into_iter().collect())
}

/// A file a check reads at a commit, as it stood there: what a bundle, which has no git history, keeps of it.
struct Kept {
    commit: String,
    path: String,
    data: Vec<u8>,
}

/// How a bundle's parts name a file it keeps as it stood at a commit.
fn at_commit_part(path: &str, commit: &str) -> String {
    format!("{path} at {}", commit.chars().take(7).collect::<String>())
}

/// The files the record's checks read at a commit, as they stood there: each only when it is the file rb verified, byte
/// for byte (by the digest it recorded), wherever `verified_at_commit` finds it.
fn kept_at_commits(led: &Ledger) -> Res<Vec<Kept>> {
    let mut kept: std::collections::BTreeMap<(String, String), Vec<u8>> = std::collections::BTreeMap::new();
    for s in checked_sources(led)? {
        let (Some(commit), Some(path), Some(res)) = (s.commit.clone().filter(|c| !c.is_empty()), s.path.clone(), s.resolved.as_ref()) else { continue };
        if s.kind != "file" || crate::sources::kept_path(&led.root, &commit, &path).is_none() || kept.contains_key(&(commit.clone(), path.clone())) {
            continue;
        }
        let file = led.root.join(&path);
        if !crate::sources::resolve_path(&file).starts_with(&led.root) {
            continue;
        }
        let data = crate::sources::verified_at_commit(&led.root, &path, &commit, &s, res).ok().map(|(d, _)| d);
        if let Some(d) = data.filter(|d| crate::sources::sha256_bytes(d) == res.sha256) {
            kept.insert((commit, path), d);
        }
    }
    Ok(kept.into_iter().map(|((commit, path), data)| Kept { commit, path, data }).collect())
}

/// Write what `kept_at_commits` read into a bundle's record, where its re-checks look for it.
fn keep(record: &Path, kept: &[Kept]) -> Res<()> {
    for k in kept {
        let Some(out) = crate::sources::kept_path(record, &k.commit, &k.path) else { continue };
        if let Some(parent) = out.parent() {
            std::fs::create_dir_all(parent).map_err(|e| io_err(e, parent))?;
        }
        std::fs::write(&out, &k.data).map_err(|e| io_err(e, &out))?;
    }
    Ok(())
}

/// What a bundle's record/.rb/.gitignore ends with when the bundle keeps files as they stood at a commit: the record's
/// own lines leave rb's cache out of git, and these let the kept files back in (with `/**`, wherever another line
/// would leave one out, as journal/ and *.tmp do), while the rest of the cache, where rb writes as it reads the bundle,
/// stays out.
const KEEP_IN_GIT: &str = "!cache/\ncache/*\n!cache/commits/\n!cache/commits/**\n";

/// Say to git that a bundle's kept files go with it, so a bundle committed and checked out again is the bundle still.
fn keep_in_git(record: &Path) -> Res<()> {
    let path = record.join(DIR).join(".gitignore");
    let mut text = std::fs::read(&path).unwrap_or_default();
    if text.ends_with(KEEP_IN_GIT.as_bytes()) {
        return Ok(());
    }
    if !text.is_empty() && !text.ends_with(b"\n") {
        text.push(b'\n');
    }
    text.extend_from_slice(KEEP_IN_GIT.as_bytes());
    std::fs::write(&path, text).map_err(|e| io_err(e, &path))
}

const TOO_BIG_FIX: &str = "A bundle carries the record and the files its checks read: point what names a large file (a setting's, a criterion's or an analysis's source) at a smaller one that states the same, then export again.";

/// What a bundle keeps free for its page, README and manifest when a source is weighed before it is copied.
const ROOM: u64 = 1 << 20;

/// A bundle past what rb clone takes, refused: `said`, then its largest parts, from what it holds as (part, bytes).
fn too_big(said: String, mut parts: Vec<(String, u64)>) -> RBError {
    parts.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    // up to three of a megabyte or more, largest first; smaller ones only when nothing is that large
    let named = parts.iter().filter(|(_, n)| *n >= 1 << 20).count().clamp(1, 3);
    let size = |n: u64| if n >= 1 << 20 { format!("{} MB", n.div_ceil(1 << 20)) } else { format!("{} KB", n.div_ceil(1 << 10)) };
    let largest: Vec<String> = parts.iter().take(named).map(|(part, n)| format!("{part} ({})", size(*n))).collect();
    RBError::msg_fix("E_WRITE_FAILED", format!("{said} It would carry {}.", largest.join(", ")), TOO_BIG_FIX)
}

/// What a refusal says of a bundle past the cap.
fn over_cap() -> String {
    format!("The bundle would be over {} MB; an rb bundle is not.", crate::zipfile::MAX_BUNDLE >> 20)
}

/// Refused when what a bundle would hold, as (part, bytes), passes what rb clone takes, naming its largest parts.
fn within_cap(parts: Vec<(String, u64)>) -> Res<()> {
    if parts.iter().map(|(_, n)| n).sum::<u64>() <= crate::zipfile::MAX_BUNDLE {
        return Ok(());
    }
    Err(too_big(over_cap(), parts))
}

/// Write `entries` as a zip at `dest`, under a name of its own beside it until it is known to be within `max` (a zip
/// is held to the cap of what it unpacks to, as rb clone holds it): refused, it names the bundle's largest `parts`,
/// and what was at `dest` stays as it was.
fn zip_to(dest: &Path, entries: &[(PathBuf, String)], parts: &[(String, u64)], max: u64) -> Res<()> {
    let name = dest.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    let aside = dest.with_file_name(format!("{name}.tmp-{}.zip", std::process::id()));
    let written = (|| -> Res<()> {
        crate::zipfile::write_zip(&aside, entries).map_err(|e| io_err(e, dest))?;
        if std::fs::metadata(&aside).map_or(false, |m| m.len() > max) {
            return Err(too_big(format!("The bundle zips to over {} MB; an rb bundle does not.", max >> 20), parts.to_vec()));
        }
        crate::sys::go_on()?;
        crate::sys::replace(&aside, dest).map_err(|e| io_err(e, dest))
    })();
    if written.is_err() {
        let _ = std::fs::remove_file(&aside);
    }
    written
}

fn copy_sources(led: &Ledger, record_dir: &Path, record_bytes: u64, kept: &[Kept]) -> Res<(Vec<String>, Vec<String>)> {
    let (mut found, mut missing) = (Vec::new(), Vec::new());
    for (rel, now) in source_paths(led)? {
        let src = led.root.join(&rel);
        let inside = crate::sources::resolve_path(&src).starts_with(&led.root);
        if !inside || !src.is_file() {
            // what the checks read only at a commit is not missing once the bundle keeps it as it stood there
            if now || !kept.iter().any(|k| k.path == rel) {
                missing.push(rel);
            }
            continue;
        }
        let bytes = std::fs::metadata(&src).map(|m| m.len()).unwrap_or(0);
        found.push((rel, src, bytes));
    }
    // a file that leaves no room for the page, README and manifest is refused before a byte of it is copied, one kept as
    // it stood at a commit too; what the copies come to together is weighed once they are made, so a file two names
    // reach is counted once
    let max = crate::zipfile::MAX_BUNDLE;
    let weighed: Vec<(String, u64)> =
        found.iter().map(|(rel, _, n)| (rel.clone(), *n)).chain(kept.iter().map(|k| (at_commit_part(&k.path, &k.commit), k.data.len() as u64))).collect();
    if let Some((_, largest)) = weighed.iter().max_by_key(|(_, n)| *n).filter(|(_, n)| *n > max - ROOM) {
        let said = if largest + record_bytes > max {
            over_cap()
        } else {
            format!("The bundle would leave less than {} MB for its page, README and manifest; an rb bundle holds {} MB at most.", ROOM >> 20, max >> 20)
        };
        return Err(too_big(said, weighed.iter().cloned().chain([("the record".to_string(), record_bytes)]).collect()));
    }
    let mut included = Vec::new();
    for (rel, src, _) in found {
        let out = record_dir.join(&rel);
        if let Some(parent) = out.parent() {
            std::fs::create_dir_all(parent).map_err(|e| io_err(e, parent))?;
        }
        copy2(&src, &out)?;
        included.push(rel);
    }
    Ok((included, missing))
}

/// What a built bundle holds, as rb clone counts it, in parts anyone knows: the record, each source by its path in
/// the repository (one kept as it stood at a commit, with the commit), the page, the README and the manifest.
fn bundle_parts(tmp: &Path) -> Vec<(String, u64)> {
    let mut parts: Vec<(String, u64)> = Vec::new();
    for (p, rel) in files_under(tmp) {
        let n = std::fs::metadata(&p).map(|m| m.len()).unwrap_or(0);
        let at_commit = rel.strip_prefix(&format!("record/{DIR}/cache/commits/")).and_then(|k| k.split_once('/'));
        let part = match at_commit {
            Some((commit, path)) => at_commit_part(path, commit),
            None if rel.starts_with(&format!("record/{DIR}/")) => "the record".to_string(),
            None => rel.strip_prefix("record/").unwrap_or(&rel).to_string(),
        };
        match parts.iter_mut().find(|(name, _)| *name == part) {
            Some(known) => known.1 += n,
            None => parts.push((part, n)),
        }
    }
    parts
}

fn git_out(root: &Path, args: &[&str]) -> Option<String> {
    let r = std::process::Command::new("git").args(args).current_dir(root).stdin(std::process::Stdio::null()).output().ok()?;
    Some(crate::rbyaml::py_strip(&String::from_utf8_lossy(&r.stdout)).to_string())
}

/// Where the code lives: origin's address where a reader can use it (`readable_code_address`), the commit that holds the code, and what
/// the working tree changes of it (`changed`). The commit is HEAD, or, where origin lacks HEAD and the commits since
/// the newest one it holds change only what the bundle carries (the record, the results folders rb reads, what a
/// clone took out of its branch), that commit: a fork's own record commit is no code. Where origin's branches, as
/// this clone last saw them, hold neither, `at_origin` is false. `building` is where this export is being made and
/// where it goes, which are rb's own, as is every page and export the ownership ledger says rb wrote.
pub fn repo_info(led: &Ledger, building: &[&Path]) -> Option<Dict> {
    let url = readable_code_address(&origin_as_written(led)?)?;
    // a repository with no commit yet names none
    let commit = git_out(&led.root, &["rev-parse", "--verify", "-q", "HEAD"])?;
    let mut d = crate::dict! {"url" => url};
    let carried = Carried::of(led, building);
    if !commit.is_empty() {
        let (at, at_origin) = code_commit(&led.root, &commit, &carried, &taken_out(led));
        d.insert("commit".into(), Value::Str(at));
        if !at_origin {
            d.insert("at_origin".into(), Value::Bool(false));
        }
    }
    d.insert("changed".into(), Value::list_of_str(changed_paths(&led.root, &carried)));
    Some(d)
}

/// origin's address as the checkout's config writes it (none without one): what an insteadOf rewrites it to for
/// this machine's own fetches is this machine's, not an address a reader is given.
fn origin_as_written(led: &Ledger) -> Option<String> {
    git_out(&led.root, &["config", "--get", "remote.origin.url"]).filter(|u| !u.is_empty())
}

/// The address a reader can fetch the code from, as rb names it on what it publishes (rb export's bundle, page and
/// manifest; rb sync sends the same): an https address, with any user:password@ before its host and any query or
/// fragment after its path taken out. Anything else (a path of this machine, file://, ssh, plain http) names no
/// address a reader can use, so none is named.
pub fn readable_code_address(url: &str) -> Option<String> {
    let url = url.trim();
    let (scheme, rest) = url.split_once("://")?;
    if !scheme.eq_ignore_ascii_case("https") {
        return None;
    }
    let end = rest.find(['/', '?', '#']).unwrap_or(rest.len());
    let (netloc, tail) = rest.split_at(end);
    let host = netloc.rsplit_once('@').map_or(netloc, |(_, h)| h);
    if host.chars().any(|c| c.is_whitespace() || c.is_control()) {
        return None;
    }
    // a host name, or [an IPv6 address], then an optional :port of digits: no name at all is no address
    let (name, port) = match host.strip_prefix('[') {
        Some(v6) => {
            let (inside, after) = v6.split_once(']')?;
            (inside, if after.is_empty() { None } else { Some(after.strip_prefix(':')?) })
        }
        None => match host.rsplit_once(':') {
            Some((n, p)) => (n, Some(p)),
            None => (host, None),
        },
    };
    let bad_name = name.is_empty() || (!host.starts_with('[') && name.contains(['[', ']', ':']));
    if bad_name || port.is_some_and(|p| p.is_empty() || !p.chars().all(|c| c.is_ascii_digit())) {
        return None;
    }
    let path = tail.split(['?', '#']).next().unwrap_or("");
    Some(format!("https://{host}{path}"))
}

/// Why an export names no code where the checkout has an origin: it is not an address a reader can use (said in
/// one line, `repo_unnamed`); none where there is no origin, or it is named, or --no-repo leaves it out.
pub fn code_unnamed(led: &Ledger) -> Option<&'static str> {
    let url = origin_as_written(led)?;
    readable_code_address(&url).is_none().then_some(CODE_UNNAMED)
}

/// What rb export says, once, where origin is not an address a reader can use.
pub const CODE_UNNAMED: &str = "The bundle names no address for the code: origin is not an https address a reader can fetch. A reader gets the code with rb clone --code <address>.";

/// What a bundle carries of the checkout, or rb wrote there of its own, as paths from the repository's root: the
/// record's folder and each results folder rb reads, the export being made, each page and export rb wrote, and each
/// instruction file rb installed that is as rb left it.
struct Carried {
    top: PathBuf,
    prefixes: Vec<String>,
}

impl Carried {
    fn of(led: &Ledger, building: &[&Path]) -> Self {
        let top = git_out(&led.root, &["rev-parse", "--show-toplevel"]).filter(|t| !t.is_empty()).map(PathBuf::from).unwrap_or_else(|| led.root.clone());
        let top = std::fs::canonicalize(&top).unwrap_or(top);
        let root = std::fs::canonicalize(&led.root).unwrap_or_else(|_| led.root.clone());
        let base = root.strip_prefix(&top).map(|r| r.to_string_lossy().replace('\\', "/")).unwrap_or_default();
        let from_top = |rel: &str| if base.is_empty() { rel.to_string() } else { format!("{base}/{rel}") };
        let mut prefixes = vec![from_top(DIR)];
        prefixes.extend(crate::repo::result_folders(&led.root).iter().map(|f| from_top(f.trim_end_matches('/'))));
        let lines = crate::owned::entries(&led.root);
        let how = |l: &Dict| l.get("how").and_then(Value::as_str).unwrap_or("").to_string();
        let path_of = |l: &Dict| l.get("path").and_then(Value::as_str).map(PathBuf::from);
        // a page or an export rb wrote; and an instruction file, a section or a server rb put in, while it is as rb left it
        let written = lines.iter().filter(|l| how(l) == "output").filter_map(path_of).chain(
            lines
                .iter()
                .filter(|l| ["created", "replaced", "section", "mcp_entry"].contains(&how(l).as_str()))
                .filter_map(path_of)
                .filter(|p| crate::owned::as_rb_wrote(&led.root, p)),
        );
        for at in building.iter().map(|b| b.to_path_buf()).chain(written) {
            let at = crate::sources::resolve_path(&at);
            let at = at.parent().and_then(|d| std::fs::canonicalize(d).ok()).map(|d| d.join(at.file_name().unwrap_or_default())).unwrap_or(at);
            if let Ok(rel) = at.strip_prefix(&top) {
                let rel = rel.to_string_lossy().replace('\\', "/");
                if !rel.is_empty() {
                    prefixes.push(rel);
                }
            }
        }
        Carried { top, prefixes }
    }

    /// Whether `path` (a file, or a folder git names whole, ending in /) is one of these or in one.
    fn holds(&self, path: &str) -> bool {
        let path = path.trim_end_matches('/');
        self.prefixes.iter().any(|p| path == p || path.starts_with(&format!("{p}/")))
    }
}

/// The paths a clone made by rb clone took out of its branch (the parent's runs' outputs), from its log.
fn taken_out(led: &Ledger) -> Vec<String> {
    let rows = led.log_entries().unwrap_or_default();
    rows.iter()
        .filter(|r| r.get("op").map(py_str).as_deref() == Some("clone"))
        .filter_map(|r| r.get("detail")?.get("code")?.get("taken_out")?.as_list().cloned())
        .flatten()
        .map(|v| py_str(&v))
        .collect()
}

/// The commit whose code HEAD's tree is, as origin holds it, and whether origin holds it. Origin is read as this
/// clone last saw its branches: with none seen, rb cannot tell, and HEAD is named as it is.
fn code_commit(root: &Path, head: &str, carried: &Carried, taken: &[String]) -> (String, bool) {
    let at_origin = |c: &str| git_out(root, &["for-each-ref", "--count=1", "--contains", c, "--format=x", "refs/remotes/origin/"]).map_or(false, |o| !o.is_empty());
    let seen = git_out(root, &["for-each-ref", "--count=1", "--format=x", "refs/remotes/origin/"]).map_or(false, |o| !o.is_empty());
    if !seen || at_origin(head) {
        return (head.to_string(), true);
    }
    // the newest commit on HEAD's first-parent line that origin holds: the first parent of the oldest it lacks
    let lacks = git_out(root, &["rev-list", "--first-parent", head, "--not", "--remotes=origin"]).unwrap_or_default();
    let base = lacks.lines().last().and_then(|c| git_out(root, &["rev-parse", "--verify", "-q", &format!("{c}^1")])).filter(|b| !b.is_empty() && at_origin(b));
    let Some(base) = base else {
        return (head.to_string(), false);
    };
    let base = base.as_str();
    let since = git_out(root, &["diff", "-z", "--no-relative", "--no-renames", "--name-only", base, head]).unwrap_or_default();
    let code_moved = since.split('\0').filter(|p| !p.is_empty()).any(|p| {
        !carried.holds(p) && !taken.iter().any(|t| p == t || (t.ends_with('/') && p.starts_with(t.as_str())) || p.starts_with(&format!("{t}/")))
    });
    if code_moved {
        (head.to_string(), false)
    } else {
        (base.to_string(), true)
    }
}

/// What the working tree changes of HEAD, as git status names it (modified, added, deleted, renamed, untracked; not
/// ignored), from the repository's root and sorted: what a rerun at the commit does not get. The record and the
/// results folders rb reads are left out, since the bundle carries them, and so is a checkout nested here.
fn changed_paths(root: &Path, carried: &Carried) -> Vec<String> {
    let o = std::process::Command::new("git")
        .args(["-c", "core.quotePath=false", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none"])
        .current_dir(root)
        .stdin(std::process::Stdio::null())
        .output();
    let Ok(o) = o else { return Vec::new() };
    if !o.status.success() {
        return Vec::new();
    }
    let text = String::from_utf8_lossy(&o.stdout).to_string();
    let mut parts = text.split('\0').filter(|p| !p.is_empty());
    let mut out: Vec<String> = Vec::new();
    while let Some(entry) = parts.next() {
        if entry.len() < 4 {
            continue;
        }
        let (xy, path) = entry.split_at(3);
        out.push(path.to_string());
        // a rename or a copy names the path it came from next
        if xy.starts_with('R') || xy.starts_with('C') {
            if let Some(from) = parts.next() {
                if xy.starts_with('R') {
                    out.push(from.to_string());
                }
            }
        }
    }
    out.retain(|p| !carried.holds(p) && !(p.ends_with('/') && carried.top.join(p).join(".git").exists()));
    out.sort();
    out.dedup();
    out
}

/// sorted(dir.rglob("*")) files, as (path, its posix path under dir).
fn files_under(dir: &Path) -> Vec<(PathBuf, String)> {
    let mut all: Vec<PathBuf> = Vec::new();
    fn walk(d: &Path, out: &mut Vec<PathBuf>) {
        for e in sorted_entries(d) {
            let p = e.path();
            out.push(p.clone());
            if e.file_type().map(|t| t.is_dir()).unwrap_or(false) {
                walk(&p, out);
            }
        }
    }
    walk(dir, &mut all);
    all.sort_by(|a, b| {
        let pa: Vec<String> = a.components().map(|c| c.as_os_str().to_string_lossy().into_owned()).collect();
        let pb: Vec<String> = b.components().map(|c| c.as_os_str().to_string_lossy().into_owned()).collect();
        pa.cmp(&pb)
    });
    all.into_iter()
        .filter(|p| p.is_file())
        .map(|p| {
            let rel = p.strip_prefix(dir).map(|r| r.to_string_lossy().replace('\\', "/")).unwrap_or_default();
            (p, rel)
        })
        .collect()
}

/// The files a bundle keeps as they stood at a commit, each as {path, commit}: what it carries of the repository's
/// history, said beside the sources it carries as they are now.
fn at_commit_list(kept: &[Kept]) -> Value {
    Value::List(kept.iter().map(|k| crate::obj! {"path" => k.path.clone(), "commit" => k.commit.clone()}).collect())
}

/// The manifest of what `tmp` holds; `sources` (what files the checks read it carries) only for a bundle with its record.
fn manifest(led: &Ledger, tmp: &Path, sources: Option<&(Vec<String>, Vec<String>)>, kept: &[Kept], repo: Option<&Dict>) -> Res<Dict> {
    let mut files = Dict::new();
    let mut total: i64 = 0;
    for (p, rel) in files_under(tmp) {
        let data = std::fs::read(&p).map_err(|e| RBError::could_not_read(&e, &crate::sys::slashed(&p)))?;
        files.insert(rel, Value::Str(crate::rbyaml::sha256_hex(&data)));
        total += data.len() as i64;
    }
    let inv = led.project()?;
    let mut man = crate::dict! {
        "format" => FORMAT, "version" => FORMAT_VERSION, "rb_version" => crate::version(), "record_format" => 1,
        "project" => crate::obj! {"id" => inv.id.clone(), "uid" => inv.uid.clone(), "title" => inv.title.clone()},
        "written" => crate::pytime::now_dt().format("%Y-%m-%d %H:%M UTC").to_string(),
        "file_count" => files.len(), "bytes" => total, "files" => Value::Dict(files)
    };
    if let Some(p) = &inv.parent {
        // without credentials or a signed query, however an earlier rb recorded it
        man.insert("parent".into(), crate::obj! {"url" => crate::http::shareable(&p.url), "cloned_at" => p.cloned_at.clone()});
    }
    if let Some((included, missing)) = sources {
        let mut carried = crate::dict! {"included" => Value::list_of_str(included.clone()), "missing" => Value::list_of_str(missing.clone())};
        if !kept.is_empty() {
            carried.insert("at_commit".into(), at_commit_list(kept));
        }
        man.insert("sources".into(), Value::Dict(carried));
    }
    if let Some(r) = repo {
        man.insert("repo".into(), Value::Dict(r.clone()));
    }
    Ok(man)
}

/// The address a reader clones: the zip itself, or the base the export directory is served from, plus /bundle.zip.
pub fn clone_url(url: &str) -> String {
    if url.ends_with(".zip") {
        url.to_string()
    } else {
        format!("{}/bundle.zip", url.trim_end_matches('/'))
    }
}

fn rmtree(p: &Path) {
    let _ = std::fs::remove_dir_all(p);
}

/// Write the export: a directory (default rb-export/ beside .rb/), or with `zip` a zip file (default rb-export.zip). It
/// holds the page alone, unless `with_record` makes it a bundle that carries the record too.
pub fn export(led: &Ledger, out: Option<&Path>, zip: bool, url: Option<&str>, no_repo: bool, with_record: bool, replace: bool) -> Res<Dict> {
    // the record is held first: a wait for it ends at once on Ctrl-C, with nothing made yet
    led.locked(|| {
        // a signal that would end rb while the export is half built ends it once that is gone
        let endings = crate::sys::catch_endings();
        let dest = destination(led, out, zip);
        // a file at --out that rb did not write is a research file: never written over unless asked (--replace)
        if zip {
            crate::owned::may_write_over(&led.root, &dest, replace, &is_rb_zip)?;
        } else if !replace {
            not_changed_since(&dest)?;
        }
        let (before, made) = (crate::owned::sha256_of(&dest), crate::owned::missing_dirs(&dest));
        let built = build(led, out, zip, url, no_repo, with_record);
        drop(endings);
        if crate::sys::interrupted() {
            crate::sys::end_interrupted();
        }
        if built.is_ok() {
            // a directory export is rb's by its manifest, a zip by its bytes
            let kind = crate::dict! {"kind" => if zip { "zip" } else { "directory" }};
            crate::owned::wrote(&led.root, crate::owned::scope_of(&led.root, &dest), &dest, "output", before.as_deref(), &made, kind);
        }
        built
    })
}

/// An rb export folder at `dest` that holds a file rb did not write, or one changed since, is the user's work now: rb
/// export does not write over it unless asked (--replace).
fn not_changed_since(dest: &Path) -> Res<()> {
    let marked = std::fs::read_to_string(dest.join("manifest.json")).ok().and_then(|t| pyjson::loads(&t).ok()).map_or(false, |m| m.get("format").and_then(|f| f.as_str()) == Some(FORMAT));
    if !dest.is_dir() || !marked {
        return Ok(());
    }
    let mut theirs: Vec<String> = extra_files(dest).iter().filter_map(|f| crate::sources::rel_to(f, dest)).collect();
    theirs.extend(verify(dest).into_iter().filter_map(|p| p.split(": ").next().map(str::to_string)));
    if theirs.is_empty() {
        return Ok(());
    }
    theirs.sort();
    Err(RBError::msg_fix(
        "E_WRITE_FAILED",
        format!("{} holds files rb export did not write or that changed since ({}), so rb leaves it as it is.", crate::cmds::share::shown_path(dest), crate::assess::first_three(&theirs, ", ")),
        "Move those files out of it, give --out another folder, or add --replace to write the export over it (those files go with it).",
    ))
}

/// Files in an export folder its manifest does not name: but the manifest itself, and the bundle.zip a published
/// export keeps beside it (one of rb's by its own marks).
pub fn extra_files(dir: &Path) -> Vec<PathBuf> {
    let Some(man) = std::fs::read_to_string(dir.join("manifest.json")).ok().and_then(|t| pyjson::loads(&t).ok()) else { return Vec::new() };
    let named: std::collections::HashSet<String> = man.get("files").and_then(|f| f.as_dict()).map(|d| d.keys().cloned().collect()).unwrap_or_default();
    let published = man.get("url").map_or(false, |u| u.as_str().map_or(false, |s| !s.is_empty()));
    files_under(dir)
        .into_iter()
        .filter(|(p, rel)| rel != "manifest.json" && !named.contains(rel) && !(published && rel == "bundle.zip" && is_rb_zip(p)))
        .map(|(p, _)| p)
        .collect()
}

/// Whether the file at `path` is a zip rb export wrote, by its own marks: a manifest of rb's format inside it.
pub fn is_rb_zip(path: &Path) -> bool {
    crate::zipfile::read_zip(path).map_or(false, |entries| {
        entries.iter().any(|(name, data)| {
            name.ends_with("/manifest.json")
                && std::str::from_utf8(data).ok().and_then(|t| pyjson::loads(t).ok()).map_or(false, |m| m.get("format").and_then(|f| f.as_str()) == Some(FORMAT))
        })
    })
}

/// Where an export goes: `out`, else rb-export/ (or rb-export.zip) beside .rb/.
fn destination(led: &Ledger, out: Option<&Path>, zip: bool) -> PathBuf {
    match out {
        Some(o) => crate::sources::resolve_path(o),
        None => led.root.join(format!("{DEFAULT_DIR}{}", if zip { ".zip" } else { "" })),
    }
}

/// `export`, for a caller that holds the signals that end rb itself (`sys::catch_endings`): stopped by one, it takes
/// away what it built and leaves what was at `out` as it was.
pub(crate) fn build(led: &Ledger, out: Option<&Path>, zip: bool, url: Option<&str>, no_repo: bool, with_record: bool) -> Res<Dict> {
    let dest = destination(led, out, zip);
    if dest.starts_with(&led.dir) {
        return Err(RBError::msg_fix(
            "E_USAGE",
            format!("An export cannot be written inside {DIR}/."),
            "Pick a destination outside the record, or leave --out off for rb-export/ beside it.",
        ));
    }
    // published without what the address may carry for its owner alone (credentials, a signed query)
    let clone_to = url.filter(|u| !u.is_empty()).map(|u| clone_url(&crate::http::shareable(u)));
    if clone_to.is_some() && !with_record {
        return Err(RBError::msg_fix(
            "E_USAGE",
            "--url is the address a reader clones the record from, and an export carries the record only with --with-record.",
            "Add --with-record to publish the record with its page, or leave --url off for the page alone.",
        ));
    }
    led.locked(|| {
        if with_record {
            refuse_unseen(led)?;
        }
        let name = dest.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        let tmp = dest.parent().map(|p| p.to_path_buf()).unwrap_or_default().join(format!("{name}.tmp-{}", std::process::id()));
        if tmp.exists() {
            rmtree(&tmp);
        }
        // the export is built beside where it goes: when that place cannot be written, it is the destination that is named
        std::fs::create_dir_all(&tmp).map_err(|e| io_err(e, &dest))?;
        let result = (|| -> Res<Dict> {
            let (made, parts) = if with_record { bundle_into(led, &tmp, &dest, clone_to.as_deref(), no_repo, zip)? } else { page_into(led, &tmp, &dest, no_repo)? };
            deliver(&tmp, &dest, &name, zip, &parts)?;
            let mut said = crate::dict! {"path" => crate::sys::slashed(&dest)};
            said.extend(made);
            said.insert("with_record".into(), Value::Bool(with_record));
            Ok(said)
        })();
        if tmp.exists() {
            rmtree(&tmp);
        }
        result
    })
}

/// A bundle in `tmp`: the record under record/, the files its checks read, the page computed from that record (with the
/// Reproduce panel when it has an address), a README, the manifest and, for a directory with an address, bundle.zip.
/// What it says of itself, and its parts as rb clone counts them.
fn bundle_into(led: &Ledger, tmp: &Path, dest: &Path, clone_to: Option<&str>, no_repo: bool, zip: bool) -> Res<(Dict, Vec<(String, u64)>)> {
    copy_tree_files(&led.dir, &tmp.join("record").join(DIR), true)?;
    let record_bytes = files_under(&tmp.join("record").join(DIR)).iter().map(|(p, _)| std::fs::metadata(p).map(|m| m.len()).unwrap_or(0)).sum();
    // a bundle has no git history: what a check reads at a commit travels as it stood there, in the record's cache
    let kept = kept_at_commits(led)?;
    let sources = copy_sources(led, &tmp.join("record"), record_bytes, &kept)?;
    keep(&tmp.join("record"), &kept)?;
    within_cap(bundle_parts(tmp))?;
    crate::sys::go_on()?;
    let repo = if no_repo { None } else { repo_info(led, &[tmp, dest]) };
    let inside = Ledger::new(&tmp.join(RECORD), &led.via, None);
    // a bundle has no git to show what made a merged record: rb's copies of what it was merged from travel with it
    let merges = led.merges_rested_on()?;
    inside.keep_merges(&merges)?;
    // the bundle's page names a file in a worktree nested here as this record's page does
    // its citations quote the checkout's files, as the plain export does: the bundle's copy has no git to read them at
    let page = crate::view::with_page_trees(crate::view::page_trees_of(led), || shell_page(&inside, clone_to, repo.as_ref(), Some(led)))?;
    crate::sys::go_on()?;
    std::fs::write(tmp.join("page.html"), page).map_err(|e| io_err(e, &tmp.join("page.html")))?;
    let mut left_behind: Vec<Value> = Vec::new();
    for r in inside.live::<Resource>()? {
        let av = Value::Dict(inside.availability(&r));
        let state = av.get("state").map(py_str).unwrap_or_default();
        if state != "present" {
            let here = led.availability(&led.load::<Resource>(&r.id)?).get("state").map(py_str).unwrap_or_default();
            left_behind.push(crate::obj! {
                "id" => r.id.clone(), "name" => r.name.clone(), "state" => state, "here" => here,
                "how" => av.get("how").filter(|h| h.truthy()).map(py_str).unwrap_or_default()
            });
        }
    }
    for local in SKIP_DIRS.iter().chain([".lock"].iter()) {
        let p = tmp.join("record").join(DIR).join(local);
        if p.is_dir() {
            rmtree(&p);
        } else if p.exists() {
            let _ = std::fs::remove_file(&p);
        }
    }
    keep(&tmp.join("record"), &kept)?; // the cache went whole, with what the page's reads left in it
    if !kept.is_empty() {
        keep_in_git(&tmp.join("record"))?;
    }
    let read_at_commit = if kept.is_empty() { "" } else { READ_AT_COMMIT };
    let merged = if merges.is_empty() { "" } else { MERGED };
    let worktrees = if keeps_worktrees(led) { WORKTREES } else { "" };
    let readme = README.to_string() + read_at_commit + merged + worktrees + &clone_to.map(|u| format!("\nThis bundle is published. Reproduce it anywhere:\n\n  rb clone {u}\n")).unwrap_or_default();
    std::fs::write(tmp.join("README.md"), readme).map_err(|e| io_err(e, &tmp.join("README.md")))?;
    let mut man = manifest(led, tmp, Some(&sources), &kept, repo.as_ref())?;
    if let Some(u) = clone_to {
        man.insert("url".into(), Value::Str(u.to_string()));
    }
    write_manifest(tmp, &man)?;
    // the bundle as rb clone counts it, the manifest included, before any zip of it is written
    let parts = bundle_parts(tmp);
    within_cap(parts.clone())?;
    crate::sys::go_on()?;
    if clone_to.is_some() && !zip {
        let id = led.project()?.id;
        let entries: Vec<(PathBuf, String)> =
            files_under(tmp).into_iter().filter(|(p, _)| p.file_name().map_or(true, |n| n != "bundle.zip")).map(|(p, rel)| (p, format!("{id}/{rel}"))).collect();
        zip_to(&tmp.join("bundle.zip"), &entries, &parts, crate::zipfile::MAX_BUNDLE)?;
    }
    let (included, missing) = sources;
    let mut said = crate::dict! {
        "file_count" => man.get("file_count").cloned().unwrap_or(Value::Int(0)), "bytes" => man.get("bytes").cloned().unwrap_or(Value::Int(0)),
        "resources_not_included" => Value::List(left_behind), "url" => clone_to.map(str::to_string), "sources_included" => Value::list_of_str(included),
        "sources_missing" => Value::list_of_str(missing), "repo" => repo.map(Value::Dict).unwrap_or(Value::Null)
    };
    if !kept.is_empty() {
        said.insert("sources_at_commit".into(), at_commit_list(&kept));
    }
    unnamed(led, no_repo, &mut said);
    Ok((said, parts))
}

/// The page alone in `tmp`, as rb page writes it, with a README saying so and a manifest hashing both: no record, no
/// file a check reads, no Reproduce panel. What it says of itself, in the keys a bundle's export says (nothing carried),
/// and its parts.
fn page_into(led: &Ledger, tmp: &Path, dest: &Path, no_repo: bool) -> Res<(Dict, Vec<(String, u64)>)> {
    let page = shell_page(led, None, None, None)?;
    crate::sys::go_on()?;
    std::fs::write(tmp.join("page.html"), page).map_err(|e| io_err(e, &tmp.join("page.html")))?;
    std::fs::write(tmp.join("README.md"), PAGE_ONLY).map_err(|e| io_err(e, &tmp.join("README.md")))?;
    let repo = if no_repo { None } else { repo_info(led, &[tmp, dest]) };
    let man = manifest(led, tmp, None, &[], repo.as_ref())?;
    write_manifest(tmp, &man)?;
    let parts = bundle_parts(tmp);
    within_cap(parts.clone())?;
    crate::sys::go_on()?;
    let mut said = crate::dict! {
        "file_count" => man.get("file_count").cloned().unwrap_or(Value::Int(0)), "bytes" => man.get("bytes").cloned().unwrap_or(Value::Int(0)),
        "resources_not_included" => Value::List(Vec::new()), "url" => Value::Null, "sources_included" => Value::List(Vec::new()),
        "sources_missing" => Value::List(Vec::new()), "repo" => repo.map(Value::Dict).unwrap_or(Value::Null)
    };
    unnamed(led, no_repo, &mut said);
    Ok((said, parts))
}

/// `repo_unnamed`: the line that says the export names no code, where origin is not an address a reader can use.
fn unnamed(led: &Ledger, no_repo: bool, said: &mut Dict) {
    if let Some(line) = (!no_repo).then(|| code_unnamed(led)).flatten() {
        said.insert("repo_unnamed".into(), Value::Str(line.to_string()));
    }
}

fn write_manifest(tmp: &Path, man: &Dict) -> Res<()> {
    let text = pyjson::dumps_with(&Value::Dict(man.clone()), &pyjson::Dumps::indent(1)).unwrap_or_default() + "\n";
    std::fs::write(tmp.join("manifest.json"), text).map_err(|e| io_err(e, &tmp.join("manifest.json")))
}

/// What was built in `tmp`, put where it goes: zipped at `dest`, each file under the zip's own name, or as the directory
/// `dest`, which takes the place only of an export rb wrote.
fn deliver(tmp: &Path, dest: &Path, name: &str, zip: bool, parts: &[(String, u64)]) -> Res<()> {
    if zip {
        let stem = name.strip_suffix(".zip").unwrap_or(name).to_string();
        let entries: Vec<(PathBuf, String)> = files_under(tmp).into_iter().map(|(p, rel)| (p, format!("{stem}/{rel}"))).collect();
        zip_to(dest, &entries, parts, crate::zipfile::MAX_BUNDLE)?;
        rmtree(tmp);
        return Ok(());
    }
    crate::sys::go_on()?;
    if dest.exists() {
        let marker = dest.join("manifest.json");
        let ours = marker.is_file()
            && std::fs::read_to_string(&marker).ok().and_then(|t| pyjson::loads(&t).ok()).map_or(false, |m| m.get("format").and_then(|f| f.as_str()) == Some(FORMAT));
        if !ours {
            return Err(RBError::msg_fix(
                "E_WRITE_FAILED",
                format!("{} exists and is not an rb export; rb will not remove it.", crate::sys::slashed(dest)),
                "Pick another --out, or move it aside.",
            ));
        }
        rmtree(dest);
    }
    crate::sys::replace(tmp, dest).map_err(|e| io_err(e, dest))
}

/// Every file in a bundle directory against its manifest; the problems, empty when it verifies. Once rb is interrupted
/// under `sys::catch_endings` it stops before the next file, with the problems found so far.
pub fn verify(bundle: &Path) -> Vec<String> {
    verify_until(bundle, &crate::sys::interrupted)
}

fn verify_until(bundle: &Path, stop: &dyn Fn() -> bool) -> Vec<String> {
    let man_path = bundle.join("manifest.json");
    if !man_path.is_file() {
        return vec![format!("{} does not exist.", crate::sys::slashed(&man_path))];
    }
    let Some(man) = std::fs::read_to_string(&man_path).ok().and_then(|t| pyjson::loads(&t).ok()) else {
        return vec![format!("{} is not valid JSON.", crate::sys::slashed(&man_path))];
    };
    let mut problems = Vec::new();
    let mut files: Vec<(String, String)> = match man.get("files") {
        Some(Value::Dict(d)) => d.iter().map(|(k, v)| (k.clone(), py_str(v))).collect(),
        _ => Vec::new(),
    };
    files.sort();
    for (rel, want) in files {
        if stop() {
            break;
        }
        let p = bundle.join(&rel);
        if !p.is_file() {
            problems.push(format!("{rel}: missing"));
        } else if std::fs::read(&p).map(|d| crate::rbyaml::sha256_hex(&d)).ok().as_deref() != Some(want.as_str()) {
            problems.push(format!("{rel}: contents differ from the manifest"));
        }
    }
    problems
}

// ---------------------------------------------------------------- the shell's export: the page the hub serves

/// What a page's footer says of it, on every page rb page and rb export have written, page.rs's and the shell's alike:
/// rb knows its own page by it where its ownership ledger has no line for the file (an older rb's).
pub const PAGE_MARK: &str = "made with rb page · a snapshot of the record at ";

const DOCTYPE: &str = "<!doctype html>";

/// Whether the file at `path` is a page rb wrote, by its own marks: the doctype it starts with and its footer's words.
pub fn is_rb_page(path: &Path) -> bool {
    std::fs::read(path).map_or(false, |b| b.starts_with(DOCTYPE.as_bytes()) && b.windows(PAGE_MARK.len()).any(|w| w == PAGE_MARK.as_bytes()))
}

/// The script rb page inlines: the export names it by its sha256 on its <html> (data-shell), so a host that serves rb's
/// own script by that name can draw a hosted page's three panes while the page itself runs nothing.
const SHELL_SCRIPT: &str = include_str!("shell/assets/script.js");

/// The shell's page of `led` as rb export writes it: its Export document, with the
/// address a reader clones from when the export is published with one, drawn, then made what the hub serves.
fn shell_page(led: &Ledger, clone_to: Option<&str>, repo: Option<&Dict>, cites_from: Option<&Ledger>) -> Res<String> {
    let mut doc = crate::shell::document(led, crate::shell::Surface::Export, None)?;
    let given = std::env::var_os("RB_TEST_SHELL_MODEL").map_or(false, |v| !v.is_empty());
    // a bundle's page is drawn from the bundle's copy of the record, which has no git: its citations are read from the
    // checkout it was exported from (`cites_from`), under the same rule for what a shared page may quote (CITE item 4)
    if let (Some(from), false) = (cites_from, given) {
        let reader = crate::ledger::cite::Reader::new(&from.root, true, false);
        let mut citations = crate::ledger::cite::of_document(&doc, &reader);
        // what a screen would hide in a quoted line is drawn as its escape, as the document's own text is
        let escaped = led.shows_escapes()? && crate::ledger::design::shown_in(&mut citations);
        if let Some(d) = doc.as_dict_mut() {
            d.insert("citations".into(), citations);
            if escaped {
                d.insert("escaped".into(), true.into());
            }
        }
    }
    let mut doc = published(doc, clone_to, given);
    // published at an address, a fork names the published project it was cloned from, and Reproduce the code the
    // record was played against: only then, as rb export always did
    if let (Some(_), Some(d)) = (clone_to, doc.as_dict_mut()) {
        let parent = led.project().ok().and_then(|p| p.parent).map(|p| crate::http::shareable(&p.url)).filter(|u| crate::http::is_address(u));
        if let (Some(parent), Some(Value::Dict(project))) = (parent, d.get_mut("project")) {
            project.insert("parent".into(), Value::Str(parent));
        }
        if let Some(repo) = repo {
            d.insert("reproduce_repo".into(), Value::Dict(repo.clone()));
        }
    }
    for_the_hub(&crate::shell::render(&doc, crate::shell::Surface::Export)?)
}

/// An Export document as it goes out: `reproduce_url` is the address a reader clones from when the export is published
/// with one (--url) and empty otherwise; without one, no resource keeps its address either, whatever the document held.
/// A test's document (`given`, RB_TEST_SHELL_MODEL) is the document a test means the shell to draw: it keeps its own
/// reproduce_url and addresses, and takes --url's address where one is given.
fn published(mut doc: Value, clone_to: Option<&str>, given: bool) -> Value {
    if given {
        if let (Some(d), Some(url)) = (doc.as_dict_mut(), clone_to) {
            d.insert("reproduce_url".into(), Value::Str(url.to_string()));
        }
        return doc;
    }
    if let Some(d) = doc.as_dict_mut() {
        d.insert("reproduce_url".into(), Value::Str(clone_to.unwrap_or_default().to_string()));
        if clone_to.is_none() {
            for r in d.get_mut("resources").and_then(Value::as_dict_mut).into_iter().flat_map(|rs| rs.values_mut()) {
                if let Some(url) = r.as_dict_mut().and_then(|r| r.get_mut("url")) {
                    *url = Value::Str(String::new());
                }
            }
        }
    }
    doc
}

/// `page` as rb export writes it for the hub: `inert`, its <html> naming the script rb page inlines. A page the hub's
/// gate still refuses is rb's own markup at fault, so it is refused and nothing is written.
pub fn for_the_hub(page: &str) -> Res<String> {
    let out = naming_the_script(&inert(page), SHELL_SCRIPT);
    let refused = hub_refusals(&out);
    if refused.is_empty() {
        return Ok(out);
    }
    Err(RBError::fault(format!("the page rb export drew holds what the hub's check refuses: {}; rb export wrote nothing", refused.join("; "))))
}

/// JavaScript's \s, which the hub's patterns are read with: wider than ASCII's, narrower than Unicode's (no U+0085).
const JS_SPACE: &str = r"(?:[\t\n\x0B\x0C\r ]|\xC2\xA0|\xE1\x9A\x80|\xE2\x80[\x80-\x8A\xA8\xA9\xAF]|\xE2\x81\x9F|\xE3\x80\x80|\xEF\xBB\xBF)";

/// A pattern of the hub's, read as JavaScript reads it without the u flag: \s as JS_SPACE, \w and \b on ASCII, case
/// folded on ASCII letters alone.
fn js(pattern: &str) -> regex::bytes::Regex {
    regex::bytes::RegexBuilder::new(&pattern.replace(r"\s", JS_SPACE)).unicode(false).case_insensitive(true).build().expect("the hub's patterns")
}

/// What trips the hub's gate (site-build web/lib/bundle-verify.ts, pageIsInert), and the two marks it takes out first.
#[derive(Clone, Copy, PartialEq, Debug)]
enum Trip {
    Active,
    HttpEquiv,
    Handler,
    JsUrl,
    Link,
    Src,
    Href,
    Style,
}

struct Gate {
    trips: Vec<(Trip, &'static str, regex::bytes::Regex)>,
    /// A data: icon, which a loading link may be.
    icon: regex::bytes::Regex,
    /// An anchor's start tag, where an absolute href may be.
    anchor: regex::bytes::Regex,
}

fn gate() -> &'static Gate {
    static GATE: std::sync::OnceLock<Gate> = std::sync::OnceLock::new();
    GATE.get_or_init(|| Gate {
        trips: vec![
            (Trip::Active, "an active element", js(r"<\s*(script|iframe|object|embed|frame\b|base\b|form\b)")),
            (Trip::HttpEquiv, "an http-equiv meta", js(r"<meta[^>]+http-equiv")),
            (Trip::Handler, "an inline event handler", js(r"\son\w+\s*=")),
            (Trip::JsUrl, "a javascript: URL", js(r"javascript:")),
            (Trip::Link, "a loading link", js(r"<link\b")),
            (Trip::Src, "an external src", js(r#"\bsrc\s*=\s*["']?\s*https?:"#)),
            (Trip::Href, "an absolute href outside a link", js(r#"\bhref\s*=\s*["']?\s*https?:"#)),
            (Trip::Style, "an external style", js(r#"@import|url\s*\(\s*["']?\s*https?:"#)),
        ],
        icon: js(r#"<link\b[^>]*href\s*=\s*"data:image/[^"]*"[^>]*>"#),
        anchor: js(r"<a\b[^>]*>"),
    })
}

/// The matches of `trip` in `b` the hub's gate counts: an href inside an anchor's start tag and a data: icon's link
/// are not refused.
fn trips_in<'a>(g: &'a Gate, trip: Trip, re: &'a regex::bytes::Regex, b: &'a [u8]) -> Vec<(usize, usize)> {
    let spared = match trip {
        Trip::Href => Some(&g.anchor),
        Trip::Link => Some(&g.icon),
        _ => None,
    };
    let spans: Vec<(usize, usize)> = spared.map(|s| s.find_iter(b).map(|m| (m.start(), m.end())).collect()).unwrap_or_default();
    re.find_iter(b).map(|m| (m.start(), m.end())).filter(|&(s, e)| !spans.iter().any(|&(a, z)| a <= s && e <= z)).collect()
}

/// Each reason the hub's gate has to refuse `page`, in the order it checks them, with the first text that trips it;
/// empty when the hub serves the page.
pub fn hub_refusals(page: &str) -> Vec<String> {
    let g = gate();
    let b = page.as_bytes();
    let mut out: Vec<String> = Vec::new();
    if !b.starts_with(DOCTYPE.as_bytes()) {
        out.push(format!("the page does not start with {DOCTYPE}"));
    }
    for (trip, why, re) in &g.trips {
        if let Some(&(s, e)) = trips_in(g, *trip, re, b).first() {
            out.push(format!("{why} ({:?})", String::from_utf8_lossy(&b[s..e])));
        }
    }
    out.extend(foreign_refusals(b));
    out
}

/// Raw text to the parser in HTML, and markup to a browser in SVG or MathML, or the reverse: the hub's gate refuses
/// each inside svg or math (pageGate, Oct 21).
const RAW_TEXT: [&str; 10] = ["style", "script", "title", "textarea", "xmp", "iframe", "noembed", "noframes", "noscript", "plaintext"];
/// What the hub's parser (htmlparser2 12) reads as raw text outside SVG and MathML: no tag inside it until its end tag.
const SPECIAL: [&str; 9] = ["script", "style", "title", "textarea", "xmp", "iframe", "noembed", "noframes", "plaintext"];
const VOID: [&str; 19] = ["area", "base", "basefont", "br", "col", "command", "embed", "frame", "hr", "img", "input", "isindex", "keygen",
    "link", "meta", "param", "source", "track", "wbr"];
/// Where SVG or MathML hands back to HTML (htmlparser2's htmlIntegrationElements, foreignObject as its SVG casing).
const INTEGRATION: [&str; 8] = ["mi", "mo", "mn", "ms", "mtext", "annotation-xml", "desc", "title"];

/// The elements an opening tag closes first, as htmlparser2's openImpliesClose has them.
fn implies_close(name: &str) -> &'static [&'static str] {
    const P: &[&str] = &["p"];
    const FORM: &[&str] = &["input", "option", "optgroup", "select", "button", "datalist", "textarea"];
    match name {
        "tr" => &["tr", "th", "td"],
        "th" => &["th"],
        "td" => &["thead", "th", "td"],
        "body" => &["head", "link", "script"],
        "a" => &["a"],
        "li" => &["li"],
        "p" => P,
        "h1" | "h2" | "h3" | "h4" | "h5" | "h6" => &["h1", "h2", "h3", "h4", "h5", "h6", "p"],
        "select" | "input" | "output" | "button" | "datalist" | "textarea" => FORM,
        "option" => &["option"],
        "optgroup" => &["optgroup", "option"],
        "dd" | "dt" => &["dd", "dt"],
        "address" | "article" | "aside" | "blockquote" | "details" | "div" | "dl" | "fieldset" | "figcaption" | "figure" | "footer"
        | "form" | "header" | "hr" | "main" | "nav" | "ol" | "pre" | "section" | "table" | "ul" => P,
        "rt" | "rp" => &["rt", "rp"],
        "tbody" | "tfoot" => &["thead", "tbody"],
        _ => &[],
    }
}

/// An attribute's value as the hub's parser hands it on, character references read; None where it holds one this
/// reader does not know, which a check then fails closed on (rb writes only &amp; &lt; &gt; &quot; and numeric ones).
fn decoded(v: &[u8]) -> Option<String> {
    let text = String::from_utf8_lossy(v);
    let mut out = String::new();
    let mut rest: &str = &text;
    while let Some(k) = rest.find('&') {
        out.push_str(&rest[..k]);
        rest = &rest[k..];
        let end = rest.find(';').filter(|e| *e <= 33);
        let Some(end) = end else {
            // htmlparser2 reads a few named references without their ';'; rb never writes one: fail closed
            if rest[1..].starts_with(|c: char| c.is_ascii_alphanumeric() || c == '#') {
                return None;
            }
            out.push('&');
            rest = &rest[1..];
            continue;
        };
        let name = &rest[1..end];
        let c = if let Some(num) = name.strip_prefix('#') {
            let code = match num.strip_prefix(['x', 'X']) {
                Some(hex) => u32::from_str_radix(hex, 16).ok()?,
                None => num.parse::<u32>().ok()?,
            };
            char::from_u32(code).unwrap_or('\u{FFFD}')
        } else {
            match name {
                "amp" => '&',
                "lt" => '<',
                "gt" => '>',
                "quot" => '"',
                "apos" => '\'',
                "lpar" => '(',
                "rpar" => ')',
                "num" => '#',
                "sol" => '/',
                "colon" => ':',
                _ => return None,
            }
        };
        out.push(c);
        rest = &rest[end + 1..];
    }
    out.push_str(rest);
    Some(out)
}

/// The hub's rule on SVG and MathML, as its pageGate reads a page through htmlparser2 12: a raw-text element inside
/// svg or math; CSS that could load a resource in a style attribute anywhere, or in any attribute holding "(" inside
/// svg or math or on one. The parser's stack is followed: an opening tag closes what it implies, "/>" closes only in
/// SVG or MathML (not inside a foreignObject or another integration point), and an end tag that closes an element
/// implicitly while inside svg or math leaves the gate unsure the svg ended, so it reads on as inside one. The first
/// of each, with what trips it.
fn foreign_refusals(b: &[u8]) -> Vec<String> {
    #[derive(Clone, Copy, PartialEq)]
    enum Ctx {
        None,
        Svg,
        Math,
    }
    let n = b.len();
    let mut open: Vec<String> = Vec::new();
    let mut ctx = vec![Ctx::None];
    let (mut foreign, mut unsure) = (0usize, false);
    let (mut raw_text, mut css) = (None::<String>, None::<String>);
    // the parser's pop: its context goes with an svg, a math or an integration point, and the gate counts the close
    let pop = |open: &mut Vec<String>, ctx: &mut Vec<Ctx>, foreign: &mut usize, unsure: &mut bool, implied: bool, in_end_tag: bool| {
        let top = open.pop().unwrap_or_default();
        if (top == "svg" || top == "math" || top == "foreignObject" || INTEGRATION.contains(&top.as_str())) && ctx.len() > 1 {
            ctx.pop();
        }
        if implied && in_end_tag && *foreign > 0 {
            *unsure = true;
        }
        if (top == "svg" || top == "math") && *foreign > 0 {
            *foreign -= 1;
        }
    };
    let mut i = 0;
    while i < n {
        if b[i] != b'<' {
            i += 1;
            continue;
        }
        if b[i..].starts_with(b"<!--") {
            i = find(b, i + 4, b"-->").map_or(n, |c| c + 3);
            continue;
        }
        let next = b.get(i + 1).copied().unwrap_or(0);
        if !(next.is_ascii_alphabetic() || next == b'!' || next == b'?' || (next == b'/' && b.get(i + 2).map_or(false, u8::is_ascii_alphabetic))) {
            i += 1;
            continue;
        }
        let tag = tag_at(b, i);
        i = tag.end;
        if tag.name.is_empty() {
            continue;
        }
        let here = *ctx.last().unwrap_or(&Ctx::None);
        // its name as the parser reads it: foreignObject in SVG's casing, image as img outside SVG and MathML
        let name = match tag.name.as_str() {
            "foreignobject" if here == Ctx::Svg || open.iter().any(|t| t == "foreignObject") => "foreignObject".to_string(),
            "image" if here == Ctx::None => "img".to_string(),
            other => other.to_string(),
        };
        if tag.closing {
            if VOID.contains(&name.as_str()) {
                continue;
            }
            if let Some(k) = open.iter().rposition(|t| *t == name) {
                while open.len() > k + 1 {
                    pop(&mut open, &mut ctx, &mut foreign, &mut unsure, true, true);
                }
                pop(&mut open, &mut ctx, &mut foreign, &mut unsure, false, true);
            }
            continue;
        }
        let closes = implies_close(&name);
        while open.last().map_or(false, |t| closes.contains(&t.as_str())) {
            pop(&mut open, &mut ctx, &mut foreign, &mut unsure, true, false);
        }
        let inside = foreign > 0 || unsure;
        if inside && RAW_TEXT.contains(&name.to_ascii_lowercase().as_str()) && raw_text.is_none() {
            raw_text = Some(format!("a style, script or text field inside SVG or MathML (\"<{}>\")", tag.name));
        }
        let svg_or_math = name == "svg" || name == "math";
        for a in &tag.attrs {
            let value = a.value.map(|(s, e)| decoded(&b[s..e])).unwrap_or(Some(String::new()));
            let checked = a.name == "style" || ((inside || svg_or_math) && value.as_ref().map_or(true, |v| v.contains('(')));
            if checked && !value.map_or(false, |v| css_is_inert(v.as_bytes())) && css.is_none() {
                css = Some(format!("CSS in an attribute that loads a resource ({:?})", String::from_utf8_lossy(&b[a.start..a.end])));
            }
        }
        if VOID.contains(&name.as_str()) {
            continue;
        }
        if here == Ctx::None && SPECIAL.contains(&name.as_str()) {
            // raw text to the parser: no tag inside until its end tag; plaintext runs to the end of the page
            i = if name == "plaintext" { n } else { find(b, i, format!("</{name}").as_bytes()).unwrap_or(n) };
        }
        open.push(name.clone());
        match name.as_str() {
            "svg" => ctx.push(Ctx::Svg),
            "math" => ctx.push(Ctx::Math),
            "foreignObject" => ctx.push(Ctx::None),
            other if INTEGRATION.contains(&other) => ctx.push(Ctx::None),
            _ => {}
        }
        if svg_or_math {
            foreign += 1;
        }
        // "/>" closes only where the parser is in SVG or MathML, as it reads after this tag
        if b[..tag.end].ends_with(b"/>") && *ctx.last().unwrap_or(&Ctx::None) != Ctx::None {
            pop(&mut open, &mut ctx, &mut foreign, &mut unsure, true, false);
        }
    }
    raw_text.into_iter().chain(css).collect()
}

/// The hub's cssIsInert, on CSS as the hub's parser hands it on (character references read): CSS escapes read,
/// comments dropped, no @import or function that fetches, every url() to the page itself or an embedded image.
fn css_is_inert(css: &[u8]) -> bool {
    // JavaScript's \s and trim(), which the hub reads CSS with: U+FEFF is a space there, U+0085 is not
    let js_space = |c: char| matches!(c, '\t' | '\n' | '\x0B' | '\x0C' | '\r' | ' ' | '\u{A0}' | '\u{1680}' | '\u{2000}'..='\u{200A}' | '\u{2028}' | '\u{2029}' | '\u{202F}' | '\u{205F}' | '\u{3000}' | '\u{FEFF}');
    const JS_S: &str = r"[\t\n\x0B\x0C\r \x{A0}\x{1680}\x{2000}-\x{200A}\x{2028}\x{2029}\x{202F}\x{205F}\x{3000}\x{FEFF}]";
    let text = String::from_utf8_lossy(css);
    let mut plain = String::new();
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        if c != '\\' {
            plain.push(c);
            continue;
        }
        let hex: String = std::iter::from_fn(|| chars.next_if(|h| h.is_ascii_hexdigit())).take(6).collect();
        if hex.is_empty() {
            match chars.next() {
                Some('\r') => {
                    chars.next_if_eq(&'\n');
                }
                Some('\n' | '\x0C') => {}
                // a backslash that ends the CSS escapes nothing, and stays
                None => plain.push('\\'),
                Some(other) => plain.push(other),
            }
        } else {
            chars.next_if(|w| js_space(*w));
            let code = u32::from_str_radix(&hex, 16).unwrap_or(0);
            plain.push(char::from_u32(if code == 0 { 0xFFFD } else { code.min(0x10FFFF) }).unwrap_or('\u{FFFD}'));
        }
    }
    let plain = regex::Regex::new(r"(?s)/\*.*?(?:\*/|$)").expect("comments").replace_all(&plain, "").to_string();
    if regex::Regex::new(&format!(r"(?i)@import\b|(?:image-set|image|expression){JS_S}*\(")).expect("fetches").is_match(&plain) {
        return false;
    }
    let local = |v: &str| {
        let v = v.trim_matches(js_space);
        v.starts_with('#') || regex::Regex::new(r"(?i)^data:image/(?:png|gif|jpeg|webp|avif|svg\+xml)[;,]").expect("image").is_match(v)
    };
    let opener = regex::Regex::new(&format!(r"(?i)url{JS_S}*\(")).expect("url");
    let p = plain.as_bytes();
    let mut from = 0;
    while let Some(m) = opener.find_at(&plain, from) {
        let mut k = m.end();
        while k < p.len() && is_space(p[k]) {
            k += 1;
        }
        let value;
        if k < p.len() && (p[k] == b'"' || p[k] == b'\'') {
            let Some(close) = plain[k + 1..].find(p[k] as char).map(|c| k + 1 + c) else { return false };
            value = &plain[k + 1..close];
            k = close + 1;
            while k < p.len() && is_space(p[k]) {
                k += 1;
            }
            if p.get(k) != Some(&b')') {
                return false;
            }
        } else {
            let Some(close) = plain[k..].find(')').map(|c| k + c) else { return false };
            value = &plain[k..close];
            k = close;
        }
        if !local(value) {
            return false;
        }
        from = k + 1;
    }
    true
}

/// What a span of a page is, to `inert`.
#[derive(Clone, Copy, PartialEq, Debug)]
enum Part {
    /// rb's markup: tags, their names, attribute names, the doctype. Never changed: a trip there stays refused.
    Markup,
    /// A text node or a comment: shown as text, so a character reference reads as the character it names.
    Text,
    /// The value of an attribute that holds words (title, aria-*, data-*, ...): read decoded, never loaded or run.
    Words,
    /// The value of any other attribute (href, src, style, ...), with the attribute's own span: a browser may load or
    /// run what it decodes to, so a value the gate trips on goes with its attribute, never encoded past the gate.
    Loads(usize, usize),
    /// A <style>'s CSS, which reads no character reference. Never changed.
    Css,
    /// A <script>, start tag to end tag.
    Script,
}

struct Span {
    start: usize,
    end: usize,
    part: Part,
}

struct Attr {
    start: usize,
    end: usize,
    name: String,
    value: Option<(usize, usize)>,
}

struct Tag {
    end: usize,
    /// Lowercase; empty for a doctype, a comment or a processing instruction.
    name: String,
    closing: bool,
    attrs: Vec<Attr>,
}

fn is_space(c: u8) -> bool {
    matches!(c, b'\t' | b'\n' | b'\x0C' | b'\r' | b' ')
}

fn find(b: &[u8], from: usize, what: &[u8]) -> Option<usize> {
    b.get(from..)?.windows(what.len()).position(|w| w.eq_ignore_ascii_case(what)).map(|k| from + k)
}

/// The tag that starts at `b[i]` ('<'), as an HTML parser reads it.
fn tag_at(b: &[u8], i: usize) -> Tag {
    let n = b.len();
    let mut j = i + 1;
    let closing = b.get(j) == Some(&b'/');
    if closing {
        j += 1;
    }
    if matches!(b.get(j), Some(b'!') | Some(b'?')) {
        return Tag { end: find(b, j, b">").map_or(n, |e| e + 1), name: String::new(), closing, attrs: Vec::new() };
    }
    let from = j;
    while j < n && !is_space(b[j]) && b[j] != b'/' && b[j] != b'>' {
        j += 1;
    }
    let name = String::from_utf8_lossy(&b[from..j]).to_ascii_lowercase();
    let mut attrs = Vec::new();
    loop {
        while j < n && (is_space(b[j]) || b[j] == b'/') {
            j += 1;
        }
        if j >= n || b[j] == b'>' {
            j = (j + 1).min(n);
            break;
        }
        let start = j;
        j += 1; // an attribute's name may start with '='
        while j < n && !is_space(b[j]) && !matches!(b[j], b'/' | b'>' | b'=') {
            j += 1;
        }
        let attr = String::from_utf8_lossy(&b[start..j]).to_ascii_lowercase();
        let mut k = j;
        while k < n && is_space(b[k]) {
            k += 1;
        }
        let mut value = None;
        if k < n && b[k] == b'=' {
            k += 1;
            while k < n && is_space(b[k]) {
                k += 1;
            }
            if k < n && (b[k] == b'"' || b[k] == b'\'') {
                let end = b[k + 1..].iter().position(|&c| c == b[k]).map_or(n, |e| k + 1 + e);
                value = Some((k + 1, end));
                j = (end + 1).min(n);
            } else {
                let from = k;
                while k < n && !is_space(b[k]) && b[k] != b'>' {
                    k += 1;
                }
                value = Some((from, k));
                j = k;
            }
        }
        attrs.push(Attr { start, end: j, name: attr, value });
    }
    Tag { end: j, name, closing, attrs }
}

/// Whether an attribute's value is words a browser shows or hands a script, never a thing it loads or runs.
fn holds_words(attr: &str) -> bool {
    attr.starts_with("data-")
        || attr.starts_with("aria-")
        || matches!(
            attr,
            "title" | "alt" | "class" | "id" | "lang" | "dir" | "role" | "name" | "value" | "label" | "placeholder" | "for" | "headers" | "abbr"
                | "summary" | "datetime" | "content" | "tabindex" | "colspan" | "rowspan" | "span" | "hidden" | "open" | "translate" | "type"
        )
}

/// The parts of `b`, in order and covering every byte, as an HTML parser with script off reads rb's own markup.
fn parts(b: &[u8]) -> Vec<Span> {
    let n = b.len();
    let mut out: Vec<Span> = Vec::new();
    let mut push = |start: usize, end: usize, part: Part| {
        if start < end {
            out.push(Span { start, end, part });
        }
    };
    let (mut i, mut text) = (0, 0);
    while i < n {
        if b[i] != b'<' {
            i += 1;
            continue;
        }
        if b[i..].starts_with(b"<!--") {
            let close = find(b, i + 4, b"-->").unwrap_or(n);
            push(text, i, Part::Text);
            push(i, i + 4, Part::Markup);
            push(i + 4, close, Part::Text);
            push(close, (close + 3).min(n), Part::Markup);
            i = (close + 3).min(n);
            text = i;
            continue;
        }
        let next = b.get(i + 1).copied().unwrap_or(0);
        let starts_a_tag = next.is_ascii_alphabetic() || next == b'!' || next == b'?' || (next == b'/' && b.get(i + 2).map_or(false, u8::is_ascii_alphabetic));
        if !starts_a_tag {
            i += 1;
            continue;
        }
        push(text, i, Part::Text);
        let tag = tag_at(b, i);
        let raw = !tag.closing && (tag.name == "script" || tag.name == "style");
        let close = if raw { find(b, tag.end, format!("</{}", tag.name).as_bytes()).unwrap_or(n) } else { tag.end };
        let close_end = if close < n && raw { tag_at(b, close).end } else { close };
        if raw && tag.name == "script" {
            push(i, close_end, Part::Script);
        } else {
            let mut at = i;
            for a in &tag.attrs {
                if let Some((vs, ve)) = a.value {
                    push(at, vs, Part::Markup);
                    push(vs, ve, if holds_words(&a.name) { Part::Words } else { Part::Loads(a.start, a.end) });
                    at = ve;
                }
            }
            push(at, tag.end, Part::Markup);
            if raw {
                push(tag.end, close, Part::Css);
                push(close, close_end, Part::Markup);
            }
        }
        i = close_end;
        text = i;
    }
    push(text, n, Part::Text);
    out
}

fn part_at(spans: &[Span], p: usize) -> Part {
    let k = spans.partition_point(|s| s.start <= p);
    spans.get(k.wrapping_sub(1)).filter(|s| p < s.end).map_or(Part::Markup, |s| s.part)
}

#[derive(Clone, Copy)]
enum Edit {
    /// One ASCII byte written as its character reference.
    Encode(usize),
    /// An attribute taken out, name to value.
    Drop(usize, usize),
}

/// Where each trip of the gate in text or in a value is broken: one character of what the pattern must match literally,
/// written as its character reference in words, or the attribute dropped where its value could load.
fn edits_for(b: &[u8], spans: &[Span]) -> Vec<Edit> {
    let g = gate();
    let mut edits = Vec::new();
    for (trip, _, re) in &g.trips {
        for (s, e) in trips_in(g, *trip, re, b) {
            let at = |c: u8| b[s..e].iter().position(|x| x.eq_ignore_ascii_case(&c)).map(|k| s + k);
            let candidates: Vec<Option<usize>> = match trip {
                Trip::Active | Trip::Link => vec![Some(s)],
                Trip::HttpEquiv => vec![Some(e - 6), Some(s)],
                Trip::Handler => vec![Some(e - 1), at(b'o')],
                Trip::JsUrl => vec![Some(e - 1), Some(s)],
                Trip::Src | Trip::Href => vec![Some(e - 1), at(b'=')],
                Trip::Style if b[s] == b'@' => vec![Some(s)],
                Trip::Style => vec![Some(e - 1), at(b'(')],
            };
            for p in candidates.into_iter().flatten() {
                match part_at(spans, p) {
                    Part::Text | Part::Words => edits.push(Edit::Encode(p)),
                    Part::Loads(a, z) => edits.push(Edit::Drop(a, z)),
                    Part::Markup | Part::Css | Part::Script => continue,
                }
                break;
            }
        }
    }
    edits
}

fn apply(b: &[u8], mut edits: Vec<Edit>) -> Vec<u8> {
    edits.sort_by_key(|e| match *e {
        Edit::Drop(a, _) => (a, 0),
        Edit::Encode(p) => (p, 1),
    });
    let mut out = Vec::with_capacity(b.len() + 5 * edits.len());
    let mut at = 0;
    for e in edits {
        match e {
            Edit::Drop(a, z) if a >= at => {
                out.extend_from_slice(&b[at..a]);
                at = z;
            }
            Edit::Encode(p) if p >= at => {
                out.extend_from_slice(&b[at..p]);
                out.extend_from_slice(format!("&#{};", b[p]).as_bytes());
                at = p + 1;
            }
            _ => {}
        }
    }
    out.extend_from_slice(&b[at..]);
    out
}

/// `page` as the hub serves it, reading as it did: no <script>, and every text and attribute value the hub's gate trips
/// on written so it does not, rb's own text included (a run value such as onnx_top1=0.76, a note's "javascript:"). A
/// character of the trip is written as its character reference, which the page shows as that character; a value that
/// could load or run (an href, a src, a style) is dropped with its attribute instead. Markup and CSS stay as drawn.
pub fn inert(page: &str) -> String {
    let b = page.as_bytes();
    let mut b: Vec<u8> = parts(b).iter().filter(|s| s.part != Part::Script).flat_map(|s| b[s.start..s.end].iter().copied()).collect();
    // each pass breaks every trip it finds; a trip whose break made another is broken on the next
    for _ in 0..8 {
        let edits = edits_for(&b, &parts(&b));
        if edits.is_empty() {
            break;
        }
        b = apply(&b, edits);
    }
    String::from_utf8(b).unwrap_or_else(|e| String::from_utf8_lossy(e.as_bytes()).into_owned())
}

/// `page` with data-shell="sha256:<the sha256 of `script`>" on its <html>, in place of any it held.
fn naming_the_script(page: &str, script: &str) -> String {
    let b = page.as_bytes();
    let mut i = 0;
    while let Some(k) = b.get(i..).and_then(|r| r.iter().position(|&c| c == b'<')).map(|k| i + k) {
        let tag = tag_at(b, k);
        if tag.name.is_empty() {
            i = tag.end.max(k + 1);
            continue;
        }
        if tag.name != "html" || tag.closing {
            break;
        }
        let named = format!(" data-shell=\"sha256:{}\"", crate::rbyaml::sha256_hex(script.as_bytes()));
        let after_name = k + "<html".len();
        // one it held goes with the space before it, so naming the script twice writes what naming it once does
        let (mut cut, upto) = tag.attrs.iter().find(|a| a.name == "data-shell").map_or((tag.end, tag.end), |a| (a.start, a.end));
        while cut > after_name && cut < upto && is_space(b[cut - 1]) {
            cut -= 1;
        }
        return [&page[..after_name], named.as_str(), &page[after_name..cut], &page[upto..]].concat();
    }
    page.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 061: the code is named only at an https address a reader can fetch, with what it carried for this machine
    /// (credentials, a query, a fragment) taken out; anything else, an https address with no host among them, names none.
    #[test]
    fn the_code_is_named_only_at_an_https_address_a_reader_can_fetch() {
        for (given, named) in [
            ("https://alice:p@ss@github.com/org/repo.git", Some("https://github.com/org/repo.git")),
            ("HTTPS://host:8443/r.git?x=1#y", Some("https://host:8443/r.git")),
            ("https://[2001:db8::1]:443/r.git", Some("https://[2001:db8::1]:443/r.git")),
            ("https://[2001:db8::1]/r.git", Some("https://[2001:db8::1]/r.git")),
            ("https://:443/srv/private/repo.git", None),
            ("https:///srv/private/repo.git", None),
            ("https://u@:443/r.git", None),
            ("https://host:/r.git", None),
            ("https://host:44x/r.git", None),
            ("https://[2001:db8::1/r.git", None),
            ("https://[]/r.git", None),
            ("https://a:b:c/r.git", None),
            ("/srv/rb/repo.git", None),
            ("file:///srv/rb/repo.git", None),
            ("http://host/r.git", None),
            ("ssh://host/r.git", None),
            ("git@host:org/r.git", None),
            ("hg::https://host/r", None),
        ] {
            assert_eq!(readable_code_address(given).as_deref(), named, "{given}");
        }
    }

    /// A folder of the test's own, gone with it.
    struct Dir(PathBuf);

    impl Dir {
        fn new() -> Dir {
            let p = std::env::temp_dir().join(format!("rb-export-test-{}-{:08x}", std::process::id(), crate::rng::os_randbits(32)));
            std::fs::create_dir_all(&p).unwrap();
            Dir(p)
        }

        fn names(&self) -> Vec<String> {
            let mut names: Vec<String> = sorted_entries(&self.0).iter().map(|e| e.file_name().to_string_lossy().into_owned()).collect();
            names.sort();
            names
        }
    }

    impl Drop for Dir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    /// A zip refused for its size never takes the place of what was there, leaves nothing beside it, and names the
    /// bundle's largest parts; one within the cap takes that place.
    #[test]
    fn a_zip_refused_for_its_size_leaves_what_was_there() {
        let d = Dir::new();
        let src = d.0.join("preds.jsonl");
        std::fs::write(&src, vec![7u8; 4096]).unwrap();
        let dest = d.0.join("pub.zip");
        std::fs::write(&dest, b"an earlier export").unwrap();
        let entries = vec![(src, "pub/record/preds.jsonl".to_string())];
        let parts = vec![("the record".to_string(), 100), ("preds.jsonl".to_string(), 4096)];
        let refused = zip_to(&dest, &entries, &parts, 100).unwrap_err();
        assert_eq!(refused.code, "E_WRITE_FAILED");
        assert!(refused.message.ends_with("It would carry preds.jsonl (4 KB)."), "{}", refused.message);
        assert_eq!(std::fs::read(&dest).unwrap(), b"an earlier export");
        assert_eq!(d.names(), ["preds.jsonl", "pub.zip"]);
        zip_to(&dest, &entries, &parts, crate::zipfile::MAX_BUNDLE).unwrap();
        assert_eq!(crate::zipfile::read_zip(&dest).unwrap(), vec![("pub/record/preds.jsonl".to_string(), vec![7u8; 4096])]);
        assert_eq!(d.names(), ["preds.jsonl", "pub.zip"]);
    }

    /// A file kept as it stood at a commit lies in the record's cache, by commit and path; a commit or a path that would
    /// lead anywhere else (up and out, from the root, empty) is kept nowhere and never read.
    #[test]
    fn a_file_kept_at_a_commit_stays_in_the_cache() {
        let root = Path::new("/bundle/record");
        let kept = crate::sources::kept_path(root, "3f9c1e2", "configs/train.json").unwrap();
        assert_eq!(kept, root.join(".rb").join("cache").join("commits").join("3f9c1e2").join("configs").join("train.json"));
        for (commit, path) in [("3f9c1e2", "../outside.json"), ("3f9c1e2", "configs/../../x"), ("3f9c1e2", "/etc/passwd"), ("3f9c1e2", "./x"), ("3f9c1e2", ""), ("../..", "x"), ("", "x"), ("abc/def", "x")] {
            assert_eq!(crate::sources::kept_path(root, commit, path), None, "{commit} {path}");
        }
    }

    /// A bundle's kept files are let into git after the record's own .gitignore lines, once however often the bundle is
    /// exported again, a last line without its newline ended first.
    #[test]
    fn a_bundle_says_once_to_git_that_its_kept_files_go_with_it() {
        let d = Dir::new();
        std::fs::create_dir_all(d.0.join(DIR)).unwrap();
        std::fs::write(d.0.join(DIR).join(".gitignore"), "cache/\njournal/").unwrap();
        keep_in_git(&d.0).unwrap();
        keep_in_git(&d.0).unwrap();
        assert_eq!(std::fs::read_to_string(d.0.join(DIR).join(".gitignore")).unwrap(), format!("cache/\njournal/\n{KEEP_IN_GIT}"));
    }

    /// A page the hub's gate passes: it runs and loads nothing, and its text may say anything.
    const CLEAN: &str = concat!(
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width\">",
        "<link rel=\"icon\" href=\"data:image/svg+xml,%3Csvg%3E%3C/svg%3E\"><title>INT8 ResNet-50 on ImageNet</title>",
        "<style>main{max-width:40em}</style></head><body><main><p>INT8 reads 75.61 top-1; text may say https:// freely, and ",
        "a link may go anywhere: <a href=\"https://www.image-net.org/\">ImageNet</a>.</p></main></body></html>"
    );

    /// The port refuses what the hub's gate refuses, for the reason it gives, reads each pattern as JavaScript does (a
    /// no-break space is a space; U+0085, a zero-width space and an accented letter are not), and passes a page that
    /// runs and loads nothing: the cases phase 0's Python port (tests/test_shell.py) holds the hub to.
    #[test]
    fn the_gate_refuses_what_the_hub_refuses() {
        assert_eq!(hub_refusals(CLEAN), Vec::<String>::new());
        let refused = [
            ("<script>1</script>", "an active element"),
            ("< iframe>", "an active element"),
            ("<FORM>", "an active element"),
            ("<base href=\"/\">", "an active element"),
            ("<meta http-equiv=\"refresh\" content=\"0\">", "an http-equiv meta"),
            ("<p onclick=\"x()\">", "an inline event handler"),
            ("seeds 2 and 3 onnx_top1=75.4", "an inline event handler"),
            ("with\u{a0}online=false", "an inline event handler"),
            ("drops javascript: links", "a javascript: URL"),
            ("<link rel=\"stylesheet\" href=\"s.css\">", "a loading link"),
            ("<img src=\"https://example.org/a.png\">", "an external src"),
            ("<div href=\"https://example.org/\">", "an absolute href outside a link"),
            ("<style>@import 'x.css'</style>", "an external style"),
            ("<style>p{background:url( \"http://example.org/a.png\")}</style>", "an external style"),
        ];
        for (text, why) in refused {
            let page = CLEAN.replace("</main>", &format!("{text}</main>"));
            let said = hub_refusals(&page);
            assert!(said.first().map_or(false, |r| r.starts_with(why)), "{text}: {said:?}");
        }
        assert!(hub_refusals(&CLEAN.replacen("<!doctype html>", "<!DOCTYPE html>", 1))[0].starts_with("the page does not start with"));
        for text in ["seeds\u{85}online=false", " on\u{e9}=1", "seeds\u{200b}online=false", "onnx_top1 75.4"] {
            assert_eq!(hub_refusals(&CLEAN.replace("</main>", &format!("{text}</main>"))), Vec::<String>::new(), "{text:?}");
        }
    }

    /// The hub's rule on SVG and MathML (Oct 21): no raw-text element inside either, a figure's old <title> included,
    /// and no CSS there that loads; one closed implicitly leaves the gate reading on as inside it. The same SVG with
    /// its words beside it, a title in the head and a local url() pass; rb export refuses the old figure as rb's fault.
    #[test]
    fn the_gate_refuses_raw_text_inside_svg_and_math() {
        let fig = "<div class=\"figw\"><svg class=\"fig\" role=\"img\" aria-label=\"criterion met\"><rect class=\"rg\" fill=\"url(#g)\" x=\"1\"/>\
                   <circle class=\"est\" r=\"5\"/><text>at least -0.01</text></svg><p class=\"figleg figv\">calib_images=128: -0.0184</p></div>";
        let clean = CLEAN.replace("</main>", &format!("{fig}</main>"));
        assert_eq!(hub_refusals(&clean), Vec::<String>::new());
        for bad in ["<circle class=\"est\" r=\"5\"><title>-0.0029</title></circle>", "<style>.a{}</style>", "<foreignObject><textarea>x</textarea></foreignObject>"] {
            let said = hub_refusals(&clean.replacen("<circle", &format!("{bad}<circle"), 1));
            assert!(said.iter().any(|r| r.starts_with("a style, script or text field inside SVG or MathML")), "{bad}: {said:?}");
        }
        for bad in ["<g fill=\"url(https://example.org/a)\"/>", "<g filter=\"u\\72l(//example.org/f)\"/>", "<g mask=\"image(x.png)\"/>"] {
            let said = hub_refusals(&clean.replacen("<circle", &format!("{bad}<circle"), 1));
            assert!(said.iter().any(|r| r.starts_with("CSS in an attribute that loads")), "{bad}: {said:?}");
        }
        assert!(!hub_refusals(&CLEAN.replace("</main>", "<math><title>t</title></math></main>")).is_empty());
        assert!(!hub_refusals(&CLEAN.replace("</main>", "<div><svg><g></div><title>t</title></main>")).is_empty(), "closed implicitly");
        let old = clean.replacen("<circle class=\"est\" r=\"5\"/>", "<circle class=\"est\" r=\"5\"><title>-0.0029</title></circle>", 1);
        let refused = for_the_hub(&old).unwrap_err();
        assert!(refused.message.contains("inside SVG or MathML") && refused.message.ends_with("rb export wrote nothing"), "{}", refused.message);
        assert!(for_the_hub(&clean).is_ok());
    }

    /// The port reads a page as the hub's parser (htmlparser2 12) does, each case as the hub's own pageGate answered it
    /// (refused: true): implied closes on an end tag and on an opening tag, "/>" ignored in a foreignObject or another
    /// integration point, values with their character references read, style attributes anywhere.
    #[test]
    fn the_foreign_rule_reads_as_the_hubs_parser() {
        let cases = [
            ("<svg><foreignObject><g/></foreignObject></svg><title>t</title>", true),
            ("<svg><p><p></p></svg><title>t</title>", false),
            ("<svg fill=\"url&#40;//example.org/x)\"></svg>", true),
            ("<svg fill=\"url(&#35;g)\"></svg>", false),
            ("<p style=\"background:image(foo.png)\">x</p>", true),
            ("<div><svg><g></div><title>t</title>", true),
            ("<svg><g></svg><title>t</title>", true),
            ("<svg/><title>t</title>", false),
            ("<svg><circle><title>x</title></circle></svg>", true),
            ("<math><mi><b/></mi></math><title>t</title>", true),
            ("<math><mi></math><title>t</title>", true),
            ("<svg><desc><g/></desc></svg><title>t</title>", true),
            ("<svg><g/><circle/></svg><title>t</title>", false),
            ("<head><title>a<svg>b</title></head><body><p>x</p></body>", false),
            ("<svg><text>a<br>b</text></svg><title>t</title>", false),
            ("<svg><style>.a{}</style></svg>", true),
            ("<noscript><style>p{}</style></noscript><svg><g/></svg>", false),
            ("<svg><a href=\"#x\"><text>y</text></a></svg><p>z</p>", false),
            ("<ul><li><svg><g></g></svg><li>b</ul><title>t</title>", false),
            ("<table><tr><td><svg></svg><td>x</table><title>t</title>", false),
            ("<svg><g fill=\"url( '#a' )\"/></svg>", false),
            ("<svg><g fill=\"url(x.png)\"/></svg>", true),
            ("<svg><g fill=\"rgb(1,2,3)\"/></svg>", false),
            ("<svg><foreignobject><p>x</p></foreignobject></svg><title>t</title>", false),
            ("<svg><image href=\"#a\"/></svg><title>t</title>", false),
            // JavaScript's spaces in CSS, which the hub reads it with (Codex pass 2)
            ("<p style=\"url(\u{85}#x)\">x</p>", true),
            ("<p style=\"u\\72\u{A0}l(x)\">x</p>", true),
            ("<p style=\"url\u{FEFF}(x)\">x</p>", true),
            ("<p style=\"url(\u{FEFF}#x)\">x</p>", false),
            ("<p style=\"a:b\\\">x</p>", false),
        ];
        for (case, refused) in cases {
            let said = foreign_refusals(format!("{DOCTYPE}{case}").as_bytes());
            assert_eq!(!said.is_empty(), refused, "{case}: {said:?}");
        }
    }

    /// A page as the shell draws it, with rb's own text and the record's that trips the hub's gate: run values
    /// (onnx_top1=, one_cycle=1) in text and in a value, a note's " online=" after a no-break space too, its
    /// "javascript:", src= and href= with an address, @import and url( in words, and a comment; and the script rb page
    /// inlines.
    const DRAWN: &str = concat!(
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><title>INT8 ResNet-50 on ImageNet</title>",
        "<link rel=\"icon\" href=\"data:image/svg+xml,%3Csvg%3E%3C/svg%3E\">",
        "<style>.on{color:red}html.app .rail{display:grid}</style><noscript><style>nav.nav{position:sticky}</style></noscript>",
        "<script>document.documentElement.classList.add('app')</script></head><body>",
        "<section class=\"view\" id=\"x-e1\" data-title=\"int8 against fp32\">",
        "<p class=\"cmdbox\" data-copy=\"rb run add e1 fp32.onnx_top1=75.69 int8.onnx_top1=75.53 one_cycle=1\">fp32 and int8 · seed 1",
        " onnx_top1=75.53 one_cycle=1</p>",
        "<p>2026-10-19 UTC: the calibration notes say online=false\u{a0}online=true and drop javascript: links; ",
        "src=https://example.org/x and href = 'http://example.org/' and @import and url( \"https://example.org/a.png\")</p>",
        "<p title=\"seed 2 onnx_top1 = 75.4\">from <a href=\"https://www.image-net.org/\">ImageNet</a> val</p><!-- onx=1 -->",
        "</section><footer>made with rb page · a snapshot of the record at 2026-10-19 09:41 UTC</footer></body></html>"
    );

    /// What a page shows: `page` with each decimal character reference read as its character.
    fn decoded(page: &str) -> String {
        let re = regex::Regex::new(r"&#(\d+);").unwrap();
        re.replace_all(page, |c: &regex::Captures| char::from_u32(c[1].parse().unwrap()).unwrap().to_string()).into_owned()
    }

    /// The export of a drawn page runs and loads nothing and passes the hub's gate, and it reads as drawn: its text,
    /// its values, its markup and its CSS are what they were, the script aside, each trip broken by one character
    /// written as its character reference; an anchor's address stays.
    #[test]
    fn the_export_runs_nothing_and_reads_as_drawn() {
        let refused = hub_refusals(DRAWN);
        for why in ["an active element", "an inline event handler", "a javascript: URL", "an external src", "an absolute href outside a link", "an external style"] {
            assert!(refused.iter().any(|r| r.starts_with(why)), "{why}: {refused:?}");
        }
        let out = for_the_hub(DRAWN).unwrap();
        assert_eq!(hub_refusals(&out), Vec::<String>::new());
        assert!(!out.to_ascii_lowercase().contains("<script"), "{out}");
        let named = format!(" data-shell=\"sha256:{}\"", crate::rbyaml::sha256_hex(SHELL_SCRIPT.as_bytes()));
        assert_eq!(decoded(&out).replacen(&named, "", 1), DRAWN.replace("<script>document.documentElement.classList.add('app')</script>", ""));
        assert!(out.contains("<style>.on{color:red}html.app .rail{display:grid}</style>") && out.contains("<a href=\"https://www.image-net.org/\">"));
        assert!(out.contains(" onnx_top1&#61;75.53 one_cycle&#61;1</p>") && out.contains("title=\"seed 2 onnx_top1 &#61; 75.4\""), "{out}");
        assert!(out.contains("javascript&#58; links") && out.contains("&#64;import") && out.contains("<!-- onx&#61;1 -->"), "{out}");
    }

    /// A value a browser could load or run goes with its attribute, never written past the gate by a character
    /// reference the browser would read back: a javascript: link, a style that loads, an image from an address. What
    /// the element holds stays.
    #[test]
    fn a_value_that_could_load_or_run_goes_with_its_attribute() {
        let drawn = concat!(
            "<!doctype html>\n<html lang=\"en\"><body><a class=\"src\" href=\"javascript:alert(1)\">a source</a>",
            "<span style=\"background:url(https://example.org/x.png)\" title=\"x\">cell</span><img alt=\"fig\" src=\"https://example.org/a.png\">",
            "<a href=\" JavaScript:void(0)\">b</a></body></html>"
        );
        let out = for_the_hub(drawn).unwrap();
        let named = format!(" data-shell=\"sha256:{}\"", crate::rbyaml::sha256_hex(SHELL_SCRIPT.as_bytes()));
        assert_eq!(
            out,
            format!("<!doctype html>\n<html{named} lang=\"en\"><body><a class=\"src\" >a source</a><span  title=\"x\">cell</span><img alt=\"fig\" ><a >b</a></body></html>")
        );
    }

    /// A trip in rb's own markup or CSS is rb's fault, never broken in place: the export is refused as a fault in rb
    /// (E_INTERNAL, exit 3, "rb itself failed"), naming it. A data: icon is not a loading link.
    #[test]
    fn rbs_own_markup_that_trips_the_gate_is_refused() {
        for (markup, why) in [
            ("<details ontoggle=\"x\"><summary>a</summary></details>", "an inline event handler"),
            ("<form><button>go</button></form>", "an active element"),
            ("<base href=\"/\">", "an active element"),
            ("<link rel=\"stylesheet\" href=\"s.css\">", "a loading link"),
            ("<style>@import 'x.css';</style>", "an external style"),
        ] {
            let refused = for_the_hub(&CLEAN.replace("</main>", &format!("{markup}</main>"))).unwrap_err();
            assert_eq!((refused.code.as_str(), refused.exit_code), ("E_INTERNAL", crate::errors::EXIT_ENVIRONMENT), "{markup}");
            assert!(refused.message.starts_with("rb itself failed: the page rb export drew holds what the hub's check refuses: ") && refused.message.contains(why), "{}", refused.message);
            assert!(refused.message.ends_with("; rb export wrote nothing"), "{}", refused.message);
        }
        assert!(for_the_hub(CLEAN).is_ok());
    }

    /// The export's <html> names by its sha256 the script rb page inlines, once: in place of any name it held, and
    /// the same however often the page is made so.
    #[test]
    fn the_export_names_the_script_rb_page_inlines() {
        let sha = crate::rbyaml::sha256_hex(b"x");
        let page = "<!doctype html>\n<html lang=\"en\"><body></body></html>";
        let named = format!("<!doctype html>\n<html data-shell=\"sha256:{sha}\" lang=\"en\"><body></body></html>");
        assert_eq!(naming_the_script(page, "x"), named);
        assert_eq!(naming_the_script(&naming_the_script(page, "y"), "x"), named);
        assert_eq!(naming_the_script("<!doctype html>\n<html><body></body></html>", "x"), format!("<!doctype html>\n<html data-shell=\"sha256:{sha}\"><body></body></html>"));
        let once = for_the_hub(DRAWN).unwrap();
        assert!(once.contains(&format!("<html data-shell=\"sha256:{}\" lang=\"en\">", crate::rbyaml::sha256_hex(SHELL_SCRIPT.as_bytes()))));
        assert_eq!(for_the_hub(&once).unwrap(), once);
    }

    /// An export's document names the address a reader clones from only when it is published with one: without
    /// --url, reproduce_url is empty and no resource keeps its address, whatever the document held.
    #[test]
    fn an_export_names_an_address_only_when_published_with_one() {
        let doc = crate::pyjson::loads(
            r#"{"tier": "owner", "reproduce_url": "https://example.org/old", "resources": {"imagenet-val": {"name": "ImageNet val", "url": "https://www.image-net.org/"}, "calib": {"name": "calibration set"}}}"#,
        )
        .unwrap();
        let alone = published(doc.clone(), None, false);
        assert_eq!(alone.get("reproduce_url").and_then(Value::as_str), Some(""));
        let res = alone.get("resources").unwrap();
        assert_eq!(res.get("imagenet-val").and_then(|r| r.get("url")).and_then(Value::as_str), Some(""));
        assert!(res.get("calib").unwrap().get("url").is_none());
        let at = "https://example.org/int8/bundle.zip";
        let out = published(doc.clone(), Some(at), false);
        assert_eq!(out.get("reproduce_url").and_then(Value::as_str), Some(at));
        assert_eq!(out.get("resources").and_then(|r| r.get("imagenet-val")).and_then(|r| r.get("url")).and_then(Value::as_str), Some("https://www.image-net.org/"));
        // a test's document is drawn as given, and takes --url's address where one is given
        assert_eq!(published(doc.clone(), None, true), doc);
        let out = published(doc, Some(at), true);
        assert_eq!(out.get("reproduce_url").and_then(Value::as_str), Some(at));
        assert_eq!(out.get("resources").and_then(|r| r.get("imagenet-val")).and_then(|r| r.get("url")).and_then(Value::as_str), Some("https://www.image-net.org/"));
    }

    /// A page is rb's by its marks, the doctype and the footer's words, whichever renderer wrote it (page.rs's, which
    /// older rbs wrote, or the shell's), as page.rs says until it goes at the switch.
    #[test]
    fn a_page_is_rbs_by_its_marks() {
        let d = Dir::new();
        let footer = "<footer>made with rb page · a snapshot of the record at 2026-10-19 09:41 UTC</footer>";
        for (name, text, rbs) in [
            ("old.html", format!("<!doctype html>\n<html lang=\"en\"><body><main>INT8</main>{footer}<script>x</script></body></html>"), true),
            ("shell.html", format!("<!doctype html>\n<html data-shell=\"sha256:00\" lang=\"en\"><body>{footer}</body></html>"), true),
            ("upper.html", format!("<!DOCTYPE html>\n<html><body>{footer}</body></html>"), false),
            ("notes.html", "<!doctype html>\n<html><body>ana's notes on the record</body></html>".to_string(), false),
        ] {
            let p = d.0.join(name);
            std::fs::write(&p, text).unwrap();
            assert_eq!(is_rb_page(&p), rbs, "{name}");
        }
        assert!(!is_rb_page(&d.0.join("missing.html")));
    }

    /// Checking a bundle against its manifest stops before the next file once it is told to (rb was interrupted).
    #[test]
    fn checking_a_bundle_stops_within_a_file_once_told() {
        let d = Dir::new();
        std::fs::write(d.0.join("manifest.json"), r#"{"files": {"a": "0", "b": "0", "c": "0"}}"#).unwrap();
        assert_eq!(verify(&d.0), ["a: missing", "b: missing", "c: missing"]);
        let asked = std::cell::Cell::new(0);
        let after_one = || {
            asked.set(asked.get() + 1);
            asked.get() > 1
        };
        assert_eq!(verify_until(&d.0, &after_one), ["a: missing"]);
    }
}
