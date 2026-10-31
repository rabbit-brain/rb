//! `rb clone`: start your own line of work from an exported bundle. The structure travels, inherited; the parent's
//! runs, analyses and decisions stay in the parent's record.

use crate::errors::{RBError, Res};
use crate::export::{verify, FORMAT, FORMAT_VERSION};
use crate::ledger::{build, build_with, find_root, Ledger, Obj, DIR, GITIGNORE};
use crate::pyjson::{self, py_str, repr_str};
use crate::record::*;
use crate::value::{Dict, Value};
use std::path::{Path, PathBuf};

const ROOT_FILE: &str = "project.yaml";

fn bundle_invalid(message: String) -> RBError {
    RBError::msg("E_BUNDLE_INVALID", message)
}

/// A folder of rb's own under the temporary directory, removed with everything in it when this is dropped.
struct Scratch(PathBuf);

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn temp_dir() -> Res<Scratch> {
    let mut last = std::io::Error::from(std::io::ErrorKind::AlreadyExists);
    for _ in 0..100 {
        let name = format!("rb-clone-{:08x}", crate::rng::os_randbits(32));
        let p = std::env::temp_dir().join(name);
        match std::fs::create_dir(&p) {
            Ok(()) => return Ok(Scratch(p)),
            Err(e) => last = e,
        }
    }
    Err(RBError::could_not_write(&last, &std::env::temp_dir().join("rb-clone")))
}

fn too_big(source: &str) -> RBError {
    bundle_invalid(format!("{source} is over {} MB; an rb bundle is not.", crate::zipfile::MAX_BUNDLE >> 20))
}

// a download is given up once a minute passes without a byte, or half an hour without it whole
const QUIET: std::time::Duration = std::time::Duration::from_secs(60);
const WHOLE: std::time::Duration = std::time::Duration::from_secs(30 * 60);

/// Download a published bundle zip, into a folder that goes when what is returned is dropped. The only network rb ever
/// touches on its own is this download, and only because the user named the address.
fn fetch(url: &str) -> Res<(Scratch, PathBuf)> {
    let hold = temp_dir()?;
    let dest = hold.0.join("bundle.zip");
    // said without what the address carried for the download alone (credentials, a signed query)
    let shown = crate::http::said(url);
    match crate::http::download(url, &dest, crate::zipfile::MAX_BUNDLE, QUIET, WHOLE) {
        Ok(()) => Ok((hold, dest)),
        Err(None) => Err(too_big(&shown)),
        Err(Some(crate::http::Failure::Unreachable(reason))) => Err(bundle_invalid(format!("{shown} could not be fetched: {reason}"))),
        // a download sends nothing of its own, so an address rb cannot ask is the only other way to fail before asking
        Err(Some(crate::http::Failure::NotAnAddress | crate::http::Failure::Unsendable)) => Err(RBError::msg_fix(
            "E_USAGE",
            format!("{shown} is not a web address: one starts with https:// (or http://) and names a host."),
            "Give the address the bundle is served from in full, or the path of a bundle on this machine.",
        )),
    }
}

/// The record directory inside a verified bundle, its manifest, and the folder rb unpacked a zip into (which goes when
/// it is dropped, so it is held while the record is read). An address is fetched first and a zip unpacked. Whatever is
/// wrong with the bundle is said of what was given (the address, the zip), never of the copy rb made of it.
fn bundle_record(given: &str) -> Res<(PathBuf, Value, Option<Scratch>)> {
    let fetched = if given.starts_with("http://") || given.starts_with("https://") { Some(fetch(given)?) } else { None };
    let mut src = fetched.as_ref().map_or_else(|| PathBuf::from(given), |(_, zip)| zip.clone());
    // an address is said without what it carried for the download alone
    let source = &crate::http::said(given);
    if !src.exists() {
        // a path that is not there is said as that: nothing is wrong with a bundle rb never saw
        return Err(RBError::msg_fix(
            "E_FILE_NOT_FOUND",
            format!("No such file or directory: {source}"),
            "Check the path; it is relative to the current directory. rb clone takes what rb export --with-record wrote (a directory or a zip), or the address one is served from.",
        ));
    }
    let unpacked = src.is_file() && src.extension().map_or(false, |e| e == "zip");
    let mut held = None;
    if unpacked {
        // reading the zip and writing what it holds fail apart: each names the file it is about
        let read = crate::zipfile::read_zip(&src);
        crate::sys::go_on()?;
        let entries = match read {
            Ok(entries) => entries,
            Err(crate::zipfile::ZipError::Bad(_)) => {
                return Err(bundle_invalid(format!("{source} is not a zip; a published bundle is the zip rb export --with-record writes.")));
            }
            Err(crate::zipfile::ZipError::TooBig) => return Err(too_big(source)),
            Err(crate::zipfile::ZipError::Unpacks) => {
                return Err(bundle_invalid(format!("{source} unpacks to over {} MB; an rb bundle does not.", crate::zipfile::MAX_BUNDLE >> 20)));
            }
            Err(crate::zipfile::ZipError::Overflows(declared)) => {
                let bytes = if declared == 1 { "byte" } else { "bytes" };
                return Err(bundle_invalid(format!("{source} holds a file that unpacks past the {declared} {bytes} it declares; an rb bundle does not.")));
            }
            Err(crate::zipfile::ZipError::Io(e)) => return Err(RBError::could_not_read(&e, source)),
        };
        drop(fetched); // the download is read: only what it unpacks to is kept
        let hold = temp_dir()?;
        let written = crate::zipfile::unpack(&entries, &hold.0);
        crate::sys::go_on()?;
        written.map_err(|e| RBError::could_not_write(&e, &hold.0))?;
        drop(entries);
        let mut inner: Vec<PathBuf> = std::fs::read_dir(&hold.0).map(|rd| rd.flatten().map(|e| e.path()).filter(|p| p.is_dir()).collect()).unwrap_or_default();
        src = if inner.len() == 1 && !hold.0.join("manifest.json").exists() { inner.remove(0) } else { hold.0.clone() };
        held = Some(hold);
    }
    let man_path = src.join("manifest.json");
    if !man_path.is_file() {
        return Err(bundle_invalid(format!("{source} has no manifest.json; a bundle is what rb export --with-record writes.")));
    }
    let man = match std::fs::read_to_string(&man_path).ok().and_then(|t| pyjson::loads(&t).ok()) {
        Some(m) => m,
        // in a directory the file is named where it is; in a zip or at an address, as the bundle's own
        None if unpacked => return Err(bundle_invalid(format!("{source} has a manifest.json that is not readable JSON."))),
        None => return Err(bundle_invalid(format!("{} is not readable JSON.", crate::sys::slashed(&man_path)))),
    };
    let format = man.get("format").cloned().unwrap_or(Value::Null);
    let version = man.get("version").cloned().unwrap_or(Value::Null);
    let ours = format.as_str() == Some(FORMAT) && matches!(version, Value::Int(v) if v == FORMAT_VERSION);
    if !ours {
        if let (Some(FORMAT), Value::Int(v)) = (format.as_str(), &version) {
            if *v > FORMAT_VERSION {
                return Err(RBError::msg("E_STATE_NEWER", format!("{source} is an rb bundle of version {v}, written by a newer rb.")));
            }
        }
        let shown = match &format {
            Value::Str(s) => repr_str(s),
            other => pyjson::py_repr(other),
        };
        return Err(bundle_invalid(format!("{source} is {shown}, not an rb bundle.")));
    }
    let problems = verify(&src);
    crate::sys::go_on()?;
    if !problems.is_empty() {
        return Err(bundle_invalid(format!("{source} does not match its manifest.")).with_problems(problems.into_iter().take(20).collect()));
    }
    let record = src.join("record");
    if !record.join(DIR).join(ROOT_FILE).is_file() {
        // an export written without --with-record lists no file of a record at all: it is a page, not a bundle
        let listed = man.get("files").and_then(Value::as_dict).map_or(false, |f| f.keys().any(|k| k.starts_with("record/")));
        if !listed {
            return Err(RBError::msg_fix(
                "E_BUNDLE_INVALID",
                format!("{source} carries a page alone, not the record: it was exported without --with-record."),
                "Clone a bundle that carries the record, which rb export --with-record writes.",
            ));
        }
        return Err(bundle_invalid(format!("{source} has no record/{DIR}/{ROOT_FILE} inside.")));
    }
    Ok((record, man, held))
}

/// A parent's setting arrives here inherited: its value and where it was stated travel, what rb read there does not.
fn inherit(s: &Setting, who: &str, at: &str, via: &str) -> Value {
    let mut d = s.dump(false);
    let status = if s.per_run || s.value.is_null() { "unknown" } else { "inherited" };
    d.insert("status".into(), Value::Str(status.into()));
    if let Some(src) = &s.source {
        let mut sd = src.dump(false);
        sd.insert("resolved".into(), Value::Null);
        d.insert("source".into(), Value::Dict(sd));
    }
    d.insert("conflict".into(), Value::Null);
    d.insert("failed".into(), Value::Null);
    d.insert("set_by".into(), Value::Str(who.into()));
    d.insert("set_at".into(), Value::Str(at.into()));
    d.insert("via".into(), Value::Str(via.into()));
    Value::Dict(d)
}

/// A clone lands in a space of its own: a git repository with the record as its first commit, like a fork.
fn make_repo(dest: &Path, project_id: &str, src: &str) -> String {
    let git = |args: &[&str], cwd: Option<&Path>| -> Option<std::process::Output> {
        let mut c = std::process::Command::new("git");
        c.args(args).stdin(std::process::Stdio::null());
        if let Some(d) = cwd {
            c.current_dir(d);
        }
        c.output().ok()
    };
    let Some(probe) = git(&["-C", &dest.to_string_lossy(), "rev-parse", "--git-dir"], None) else {
        return "git not found: version the record yourself when you can".into();
    };
    if probe.status.success() {
        return "inside an existing repository: commit .rb/ there when ready".into();
    }
    let init_ok = git(&["init", "-q"], Some(dest)).map_or(false, |o| o.status.success());
    let add_ok = init_ok && git(&["add", "-A"], Some(dest)).map_or(false, |o| o.status.success());
    if !add_ok {
        return "git failed here: version the record yourself".into();
    }
    let msg = format!("clone of {project_id} from {src}");
    crate::sources::forget_git(); // the repository is new: nothing git said of this place before holds
    // never under an identity git guesses from the account and host: without one in its config the record stays staged
    match git(&["-c", "user.useConfigOnly=true", "commit", "-q", "-m", &msg], Some(dest)) {
        None => "git failed here: version the record yourself".into(),
        Some(o) if o.status.success() => "a repository of its own; the record is its first commit".into(),
        Some(_) => "a repository of its own; the record is staged: commit it as yourself (git config user.name and user.email first)".into(),
    }
}

/// The branch a clone that got the code checks it out on, at the commit.
const BRANCH: &str = "rerun";

/// What marks a reason as git's own message, where get_code failed in git: rb clone says it after "git said:".
const GIT_SAID: char = '\u{1}';

/// Why the code is not got where git failed on an address rb cannot read safely: rb's own words, with no git message.
const UNREADABLE_WHY: &str = "git failed on it; its message is left out, since git, curl and ssh name the host they tried, and in this address rb cannot tell the host from a password or a token";

/// What git said when it failed, in one line: the paragraph that holds its error (git's advice after a blank line
/// left out, as is its `fatal: `), each line ending in a stop. Its last line alone can be a fragment of that advice
/// ("and the repository exists.").
fn git_message(stderr: &str) -> String {
    let lines: Vec<&str> = stderr.lines().map(str::trim).collect();
    let paragraphs: Vec<&[&str]> = lines.split(|l| l.is_empty()).filter(|p| !p.is_empty()).collect();
    let error = |p: &[&str]| p.iter().any(|l| l.starts_with("fatal: ") || l.starts_with("error: "));
    let Some(para) = paragraphs.iter().copied().find(|p| error(p)).or_else(|| paragraphs.first().copied()) else { return "git failed".to_string() };
    let said: Vec<String> = para
        .iter()
        .map(|l| l.trim_start_matches("fatal: ").trim_start_matches("error: "))
        .map(|l| if l.ends_with(['.', '!', '?']) { l.to_string() } else { format!("{l}.") })
        .collect();
    said.join(" ")
}

/// An address as rb says it and keeps it: a web address without what it carried for the download alone, and a path
/// on this machine with `/` between its parts, as rb writes every path (on Windows `C:\code\int8.git` is
/// `C:/code/int8.git`, which git reads too).
fn said_address(a: &str) -> String {
    if a.contains("://") {
        crate::http::said(a)
    } else {
        crate::sys::typed(a)
    }
}

/// git's message with the address it quotes said as rb says it, so what the address carried for the download alone
/// (a password, a token) is never printed or written.
fn without_address(message: &str, address: &str) -> String {
    if address.is_empty() {
        return message.to_string();
    }
    let said = said_address(address);
    let message = replace_whole(message, address, &said);
    // git takes the credentials out of an address it quotes itself, up to the first @ before the first /, and keeps
    // the rest (a signed query, a user's own @): each form git or curl may quote is said as rb says the address too
    let Some((front, rest)) = address.split_once("://") else { return message };
    let git = rest.find('/').map_or(rest, |end| &rest[..end]);
    let curl = rest.find(['/', '?', '#']).map_or(rest, |end| &rest[..end]);
    let mut forms: Vec<String> = [git.find('@'), curl.rfind('@')].into_iter().flatten().map(|at| format!("{front}://{}", &rest[at + 1..])).collect();
    // the longest first, so a shorter form inside a longer one never leaves the rest of it behind
    forms.sort_by_key(|f| std::cmp::Reverse(f.len()));
    forms.dedup();
    let message = forms.iter().fold(message, |m, form| replace_whole(&m, form, &said));
    // what is left of the address or a form that says more than rb says (a token with a /, a signed query), ended or
    // not, is said as rb says it all the same: no part of a password or a token is said, even inside a longer address
    let mut all: Vec<&str> = std::iter::once(address).chain(forms.iter().map(String::as_str)).filter(|f| *f != said).collect();
    all.sort_by_key(|f| std::cmp::Reverse(f.len()));
    let message = all.iter().fold(message, |m, f| m.replace(f, &said));
    // the credentials go wherever they still start a host, in the whole form and in each form git leaves after an @
    // of theirs: git:// and ssh name the host with no scheme before it
    let shared = crate::http::shareable(address);
    let kept = shareable_rest(&shared, front);
    let cut = std::iter::once(0).chain(rest.match_indices('@').map(|(i, _)| i + 1)).find(|&k| rest[k..].starts_with(kept)).unwrap_or(0);
    let secret = &rest[..cut];
    let mut secrets: Vec<&str> = std::iter::once(secret).chain(secret.match_indices('@').map(|(i, _)| &secret[i + 1..])).filter(|x| x.len() > 1).collect();
    secrets.sort_by_key(|x| std::cmp::Reverse(x.len()));
    let message = secrets.iter().fold(message, |m, x| cut_before_host(&m, x));
    // where rb says no address, git's message names no host either: the host git, curl or ssh may have read out of it
    // ("Could not resolve host: tok") can be part of a token
    if said != crate::http::UNREADABLE {
        return message;
    }
    // each as read, and as curl and ssh may print it: its escapes decoded, a lone % escaped, an IPv6 host without its
    // brackets, and in any case; a name with letters beyond ASCII curl prints as punycode, so then any such name goes
    let mut hosts: Vec<String> = Vec::new();
    for read in read_hosts(rest) {
        let decoded = percent_decoded(read);
        let bare = decoded.strip_prefix('[').and_then(|v6| v6.strip_suffix(']')).map(str::to_string);
        hosts.extend([read.to_string(), lone_percent_escaped(read), lone_percent_escaped(&decoded), decoded].into_iter().chain(bare));
    }
    hosts.retain(|h| !h.is_empty());
    hosts.sort_by_key(|h| std::cmp::Reverse(h.len()));
    hosts.dedup();
    let punycode = hosts.iter().any(|h| !h.is_ascii());
    let message = message
        .split(crate::http::UNREADABLE)
        .map(|piece| {
            let piece = hosts.iter().fold(piece.to_string(), |m, h| replace_token(&m, h, crate::http::UNREADABLE));
            if punycode {
                replace_punycode(&piece, crate::http::UNREADABLE)
            } else {
                piece
            }
        })
        .collect::<Vec<_>>()
        .join(crate::http::UNREADABLE);
    // and whatever stands where git, curl or ssh name the host they tried is said as the phrase, whatever form of the
    // address they turned it into (curl maps letters beyond ASCII to ASCII ones, and writes escapes back its own way)
    let mut message = replace_host_slots(&message, crate::http::UNREADABLE);
    // a user's part before the host, said as rb says it too, leaves the phrase twice around its @: said once
    let twice = format!("{0}@{0}", crate::http::UNREADABLE);
    while message.contains(&twice) {
        message = message.replace(&twice, crate::http::UNREADABLE);
    }
    message
}

/// Where git, curl and ssh name the host they tried: "Could not resolve host: X", "Failed to connect to X port 443",
/// "unable to look up X (port 9418)", "ssh: Could not resolve hostname X: ...", "connect to host X port 22",
/// "Connection to X closed", "Connection closed by X".
const HOST_SLOTS: [&str; 7] = ["resolve host: ", "resolve hostname ", "connect to host ", "connect to ", "look up ", "connection to ", "connection closed by "];

/// `text` with the name in each of `HOST_SLOTS` (up to a space, a quote or a bracket, without a stop, comma, colon or
/// semicolon that ends it) replaced by `to`, where it is not `to` already.
fn replace_host_slots(text: &str, to: &str) -> String {
    let lower = text.to_ascii_lowercase();
    let mut cuts: Vec<(usize, usize)> = Vec::new();
    for slot in HOST_SLOTS {
        for (at, _) in lower.match_indices(slot) {
            let start = at + slot.len();
            let longer = slot == "connect to " && lower[start..].starts_with("host ");
            if longer || text[start..].starts_with(to) || cuts.iter().any(|&(s, e)| start >= s && start < e) {
                continue;
            }
            let end = text[start..].find(|c: char| c.is_whitespace() || matches!(c, '\'' | '"' | '`' | '(' | ')')).map_or(text.len(), |e| start + e);
            let name = text[start..end].trim_end_matches(['.', ',', ':', ';']);
            // curl's own "Couldn't connect to server" names none
            if !name.is_empty() && !(slot == "connect to " && name == "server") {
                cuts.push((start, start + name.len()));
            }
        }
    }
    cuts.sort();
    let mut out = String::with_capacity(text.len());
    let mut last = 0;
    for (s, e) in cuts {
        if s < last {
            continue;
        }
        out.push_str(&text[last..s]);
        out.push_str(to);
        last = e;
    }
    out.push_str(&text[last..]);
    out
}

/// Each `%` not followed by two hex digits written `%25`, as curl writes it back.
fn lone_percent_escaped(text: &str) -> String {
    let b = text.as_bytes();
    let mut out = String::with_capacity(text.len());
    for (i, c) in text.char_indices() {
        let escape = c == '%' && b.get(i + 1).map_or(false, u8::is_ascii_hexdigit) && b.get(i + 2).map_or(false, u8::is_ascii_hexdigit);
        out.push_str(if c == '%' && !escape { "%25" } else { &text[i..i + c.len_utf8()] });
    }
    out
}

/// `%XX` escapes decoded, as curl decodes them in a host before it names it.
fn percent_decoded(text: &str) -> String {
    let b = text.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        let hex = |c: u8| (c as char).to_digit(16);
        match (b[i], b.get(i + 1).copied().and_then(hex), b.get(i + 2).copied().and_then(hex)) {
            (b'%', Some(hi), Some(lo)) => {
                out.push((hi * 16 + lo) as u8);
                i += 3;
            }
            (c, _, _) => {
                out.push(c);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// `text` with every name that holds a punycode label (`xn--...`) replaced by `to`.
fn replace_punycode(text: &str, to: &str) -> String {
    let name = |c: char| c.is_ascii_alphanumeric() || c == '-' || c == '_' || c == '.';
    let mut out = String::with_capacity(text.len());
    let mut last = 0;
    let mut at = 0;
    while at < text.len() {
        let c = text[at..].chars().next().unwrap_or(' ');
        if !name(c) {
            at += c.len_utf8();
            continue;
        }
        let end = text[at..].find(|c: char| !name(c)).map_or(text.len(), |e| at + e);
        let run = text[at..end].trim_end_matches('.');
        if run.split('.').any(|label| label.len() > 4 && label[..4].eq_ignore_ascii_case("xn--")) {
            out.push_str(&text[last..at]);
            out.push_str(to);
            last = at + run.len();
        }
        at = end;
    }
    out.push_str(&text[last..]);
    out
}

/// The hosts git, curl and ssh may read out of an address's part after `://` (an extra `/` there skipped, as curl
/// skips it), and the user's part they may print before one: in the authority up to the first `/`, `?` or `#` (curl)
/// and up to the first `/` (git, ssh), every run that starts at its start or after an @ and ends at an @ or its end
/// (`u:p@tok`, `u:p`, `tok`), each with what follows a `:` and without it (git names `user:pass` as a host and its
/// port).
fn read_hosts(rest: &str) -> Vec<&str> {
    fn upto<'a>(r: &'a str, stops: &[char]) -> &'a str {
        r.find(stops).map_or(r, |e| &r[..e])
    }
    fn no_port(h: &str) -> &str {
        match h.strip_prefix('[') {
            Some(v6) => v6.split_once(']').map_or(h, |(inside, _)| &h[..inside.len() + 2]),
            None => h.split(':').next().unwrap_or(h),
        }
    }
    let rest = rest.trim_start_matches('/');
    let mut read = Vec::new();
    for authority in [upto(rest, &['/', '?', '#']), upto(rest, &['/'])] {
        let ats: Vec<usize> = authority.match_indices('@').map(|(i, _)| i).collect();
        let starts = std::iter::once(0).chain(ats.iter().map(|i| i + 1));
        for start in starts {
            for end in ats.iter().copied().filter(|&e| e >= start).chain(std::iter::once(authority.len())) {
                read.push(&authority[start..end]);
            }
        }
    }
    read.into_iter().flat_map(|h| [h, no_port(h)]).collect()
}

/// `text` with `token` replaced by `to` wherever it stands as a whole name, in any ASCII case (ssh says a host in
/// lower case): not inside a longer name (a letter, a digit, `-`, `_`, or a `.` before more of a name, on either side).
fn replace_token(text: &str, token: &str, to: &str) -> String {
    let name = |c: char| c.is_alphanumeric() || c == '-' || c == '_';
    let mut out = String::with_capacity(text.len());
    let mut last = 0;
    let (lower, wanted) = (text.to_ascii_lowercase(), token.to_ascii_lowercase());
    for (at, _) in lower.match_indices(&wanted) {
        if at < last {
            continue;
        }
        let end = at + token.len();
        let before = text[..at].chars().next_back();
        let mut after = text[end..].chars();
        let next = after.next();
        let inside_before = before.map_or(false, |c| name(c) || (c == '.' && text[..at - 1].chars().next_back().map_or(false, name)));
        let inside_after = next.map_or(false, |c| name(c) || (c == '.' && after.next().map_or(false, name)));
        if !inside_before && !inside_after {
            out.push_str(&text[last..at]);
            out.push_str(to);
            last = end;
        }
    }
    out.push_str(&text[last..]);
    out
}

/// What `shareable` kept after the scheme of an address written `front://...`.
fn shareable_rest<'a>(kept: &'a str, front: &str) -> &'a str {
    kept.strip_prefix(front).and_then(|k| k.strip_prefix("://")).unwrap_or(kept)
}

/// `text` without `secret` wherever it starts a host: at the start, or after a space, a quote, a `/` or a `(`.
fn cut_before_host(text: &str, secret: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut last = 0;
    for (at, _) in text.match_indices(secret) {
        if at < last {
            continue;
        }
        if text[..at].chars().next_back().map_or(true, |c| matches!(c, '/' | '\'' | '"' | '`' | '(') || c.is_whitespace()) {
            out.push_str(&text[last..at]);
            last = at + secret.len();
        }
    }
    out.push_str(&text[last..]);
    out
}

/// `text` with `from` replaced by `to` only where `from` ends the address it is in: at a quote, a space, a / or the
/// end of the text. A longer address that starts with the same text is kept whole, never rewritten in part.
fn replace_whole(text: &str, from: &str, to: &str) -> String {
    if from.is_empty() {
        return text.to_string();
    }
    let mut out = String::with_capacity(text.len());
    let mut last = 0;
    for (at, _) in text.match_indices(from) {
        if at < last {
            continue;
        }
        let end = at + from.len();
        if text[end..].chars().next().map_or(true, |c| c == '/' || c == '\'' || c == '"' || c == '`' || c.is_whitespace()) {
            out.push_str(&text[last..at]);
            out.push_str(to);
            last = end;
        }
    }
    out.push_str(&text[last..]);
    out
}

/// A reason get_code gave, as rb's words and as git's own message, apart: (why, git_said).
fn said_apart(why: &str) -> (String, String) {
    match why.strip_prefix(GIT_SAID) {
        Some(line) => (line.to_string(), line.to_string()),
        None => (why.to_string(), String::new()),
    }
}

/// Why the code is not got where git has no identity of the user's own.
const NO_IDENTITY: &str = "git has no user.name and user.email set here, and the record must be committed in place of the parent's there; set them (git config --global user.name and user.email), then clone again";

/// Whether git would commit here under an identity of the user's own (its config or environment), never one it guesses.
fn has_identity() -> bool {
    ["GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"].iter().all(|v| {
        std::process::Command::new("git")
            .args(["-c", "user.useConfigOnly=true", "var", v])
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status()
            .map_or(false, |s| s.success())
    })
}

/// A path from the top of a checkout as git names it: no `.` or empty part (`./results/a.json` is `results/a.json`), a
/// folder's trailing `/` kept.
fn plain_path(p: &str) -> String {
    let folder = p.ends_with('/');
    let parts: Vec<&str> = p.split('/').filter(|x| !x.is_empty() && *x != ".").collect();
    format!("{}{}", parts.join("/"), if folder && !parts.is_empty() { "/" } else { "" })
}

/// What a rerun may need, by path from the top of the parent's checkout: where each resource the record keeps is kept
/// here (a run's input or not: the rerun gets each again), and each database a run's row was read from. A clone never
/// takes one out of the code's branch.
fn reads_of(runs: &[Run], resources: &[Resource]) -> Vec<String> {
    let mut kept: Vec<String> = resources
        .iter()
        .flat_map(|res| res.locations.iter().filter(|l| l.provider == "local").map(|l| plain_path(&l.r#ref).trim_end_matches('/').to_string()))
        .collect();
    kept.extend(runs.iter().filter_map(|r| r.receipt.row.as_ref()).map(|w| plain_path(&w.path).trim_end_matches('/').to_string()));
    kept.sort();
    kept.dedup();
    kept
}

/// Every repository file the record's checks read: each live setting's source and the source its cited value names,
/// each live criterion's and analysis's source, by path from the top of the checkout.
fn cited_paths(led: &Ledger) -> Res<Vec<String>> {
    let mut sources: Vec<Source> = Vec::new();
    for c in led.all::<Criterion>()?.iter().filter(|c| c.retracted.is_none()) {
        sources.extend(c.source.clone());
    }
    for e in led.all::<Experiment>()?.iter().filter(|e| e.retracted.is_none()) {
        for s in e.settings.iter().chain(e.variants.iter().flat_map(|v| v.settings.iter())).filter(|s| s.retracted.is_none()) {
            sources.extend(s.source.clone());
            sources.extend(s.cited.as_ref().and_then(|c| c.source.clone()));
        }
    }
    for a in led.all::<Analysis>()?.iter().filter(|a| a.retracted.is_none()) {
        sources.extend(a.source.clone());
        sources.extend(a.comparisons.iter().filter_map(|c| c.source.clone()));
    }
    Ok(sources
        .into_iter()
        .filter(|s| s.kind == "file" || s.kind == "run")
        .filter_map(|s| s.path)
        .map(|p| plain_path(&p).trim_end_matches('/').to_string())
        .filter(|p| !p.is_empty() && p != ".")
        .collect())
}

/// Whether a file is source code by its suffix (a script, a notebook, a module): a folder that holds one is never the
/// parent's results alone.
fn is_code(path: &str) -> bool {
    const CODE: &[&str] = &["py", "ipynb", "sh", "bash", "zsh", "r", "jl", "js", "mjs", "ts", "rs", "go", "c", "cc", "cpp", "cu", "h", "hpp", "java", "scala", "kt", "lua", "pl", "rb", "m", "swift"];
    let name = path.rsplit('/').next().unwrap_or(path);
    name.rsplit_once('.').map_or(false, |(stem, ext)| !stem.is_empty() && CODE.contains(&ext.to_ascii_lowercase().as_str()))
}

/// Whether `path` (a folder where it ends with `/`) is, holds or sits in one of `reads`.
fn holds_a_read(reads: &[String], path: &str) -> bool {
    reads.iter().any(|k| k == path.trim_end_matches('/') || (path.ends_with('/') && k.starts_with(path)) || path.starts_with(&format!("{k}/")))
}

/// What the parent's runs wrote, by path from the top of its checkout: each file a run names in a folder where results
/// land, and in its place the run's own folder there (`results/r004_plain_s0/` for its metrics.json), since what else
/// is there the run wrote too. Never a file elsewhere, which may be code, nor what the runs read (`reads_of`: the
/// database a row was read from, which is no run's own, and what a resource the runs read is kept as), nor a folder
/// that holds one.
fn outputs_of(runs: &[Run], reads: &[String]) -> Vec<String> {
    let holds = |path: &str| holds_a_read(reads, path);
    let mut out: Vec<String> = Vec::new();
    for r in runs {
        let row = r.receipt.row.as_ref().map(|w| w.path.as_str());
        for f in r.files.iter().filter(|f| Some(f.path.as_str()) != row) {
            // a run's code, kept as an artifact, is never taken out
            let plain = plain_path(&f.path);
            if is_code(&plain) {
                continue;
            }
            let parts: Vec<&str> = plain.split('/').collect();
            if parts.len() < 2 || !crate::journal::DEFAULT_DIRS.contains(&parts[0]) || parts.contains(&"..") {
                continue;
            }
            let folder = format!("{}/{}/", parts[0], parts[1]);
            let path = if parts.len() > 2 && !holds(&folder) { folder } else { plain.clone() };
            if !holds(&path) && !out.contains(&path) {
                out.push(path);
            }
        }
    }
    out.sort();
    out
}

/// Get the code into `dest`: a clone of `address` at `commit` (its default branch's commit where none is named) on a
/// new branch, with what is the parent's taken out of the branch: the record that commit holds, what its runs wrote
/// (`outputs`), and every other results folder the commit holds (one rb reconcile would read: results no recorded run
/// named), so the runs made here write their own and rb reconcile reads only theirs; git keeps all of it at the commit.
/// Never what the runs read (`reads`), nor a folder that holds one, nor source code: a run's own folder that holds code
/// gives up only the files the runs named in it (`named`). The full commit and the paths the commit held that
/// were taken out, or what git said where it failed; then nothing of it is left at `dest`.
fn get_code(dest: &Path, address: &str, commit: Option<&str>, given: &[String], named: &[String], reads: &[String]) -> Result<(String, Vec<String>), String> {
    let existed = dest.exists();
    let git = |args: &[&str]| -> Result<String, String> {
        let o = std::process::Command::new("git").args(args).stdin(std::process::Stdio::null()).output().map_err(|_| "git not found".to_string())?;
        if o.status.success() {
            return Ok(String::from_utf8_lossy(&o.stdout).trim().to_string());
        }
        // where rb says no address, rb says why in its own words and leaves git's message out: git, curl and ssh
        // name the host they tried, which can be part of a password or a token there
        if said_address(address) == crate::http::UNREADABLE {
            return Err(UNREADABLE_WHY.to_string());
        }
        Err(format!("{GIT_SAID}{}", without_address(&git_message(&String::from_utf8_lossy(&o.stderr)), address)))
    };
    let d = dest.to_string_lossy().to_string();
    let done = (|| {
        git(&["clone", "-q", "--no-checkout", "--", address, &d])?;
        let at = commit.unwrap_or("HEAD");
        let full = git(&["-C", &d, "rev-parse", "--verify", "-q", &format!("{at}^{{commit}}")]).map_err(|_| format!("it does not hold commit {}", at.chars().take(12).collect::<String>()))?;
        git(&["-C", &d, "checkout", "-q", "-b", BRANCH, &full])?;
        if dest.join(DIR).exists() {
            let _ = git(&["-C", &d, "rm", "-r", "-q", "--", DIR]);
            let _ = std::fs::remove_dir_all(dest.join(DIR));
        }
        // a folder that holds source code, which the runs may need, is never taken out whole: in a run's own folder
        // only the files the runs named go
        let code_in = |folder: &str| git(&["-C", &d, "ls-files", "-z", "--", folder]).unwrap_or_default().split('\0').any(is_code);
        let mut outputs: Vec<String> = Vec::new();
        for o in given {
            if o.ends_with('/') && code_in(o) {
                outputs.extend(named.iter().filter(|f| f.starts_with(o.as_str()) && !is_code(f) && !holds_a_read(reads, f)).cloned());
            } else {
                outputs.push(o.clone());
            }
        }
        // the results folders the commit holds that no run named, beside what the runs wrote
        for folder in crate::repo::result_folders(dest) {
            if holds_a_read(reads, &folder) || outputs.iter().any(|o| folder.starts_with(o.as_str()) || o.starts_with(folder.as_str())) {
                continue;
            }
            if !code_in(&folder) {
                outputs.push(folder);
            }
        }
        outputs.sort();
        outputs.dedup();
        let mut taken: Vec<String> = Vec::new();
        for chunk in outputs.chunks(200) {
            let mut args = vec!["-C", d.as_str(), "ls-files", "-z", "--"];
            args.extend(chunk.iter().map(String::as_str));
            let held = git(&args).unwrap_or_default();
            let held: Vec<&str> = held.split('\0').filter(|p| !p.is_empty()).collect();
            let theirs: Vec<&String> = chunk.iter().filter(|o| held.iter().any(|h| *h == o.as_str() || (o.ends_with('/') && h.starts_with(o.as_str())))).collect();
            if theirs.is_empty() {
                continue;
            }
            let mut rm = vec!["-C", d.as_str(), "rm", "-r", "-q", "--"];
            rm.extend(theirs.iter().map(|s| s.as_str()));
            git(&rm)?;
            taken.extend(theirs.into_iter().cloned());
        }
        Ok((full, taken))
    })();
    if done.is_err() {
        let _ = std::fs::remove_dir_all(dest);
        if existed {
            let _ = std::fs::create_dir_all(dest);
        }
    }
    crate::sources::forget_git();
    done
}

/// The record's first commit on the code's branch, in place of the record that commit held: it changes the record
/// alone, so the code the runs are recorded at is the commit's. Never under an identity git guesses: without one in
/// its config the record stays staged.
fn commit_record(dest: &Path, project_id: &str, src: &str, commit: &str) -> String {
    let short: String = commit.chars().take(12).collect();
    let git = |args: &[&str]| std::process::Command::new("git").args(args).current_dir(dest).stdin(std::process::Stdio::null()).output().ok();
    if !git(&["add", "-A", "--", DIR]).map_or(false, |o| o.status.success()) {
        return format!("a checkout of the code at {short}, on branch {BRANCH}; git failed here: commit {DIR}/ yourself before anything else");
    }
    let msg = format!("rerun of {project_id} from {src}: its record, inherited");
    crate::sources::forget_git();
    match git(&["-c", "user.useConfigOnly=true", "commit", "-q", "-m", &msg]) {
        Some(o) if o.status.success() => format!("a checkout of the code at {short}, on branch {BRANCH}; the record is its latest commit, in place of the parent's, which git keeps at {short}"),
        _ => format!("a checkout of the code at {short}, on branch {BRANCH}; the record is staged in place of the parent's: commit it as yourself before anything else (git config user.name and user.email first)"),
    }
}

/// What a clone takes from the parent's record, read whole while the bundle is at hand.
struct Parent {
    man: Value,
    proj: Project,
    questions: Vec<Question>,
    metrics: Vec<Metric>,
    resources: Vec<Resource>,
    experiments: Vec<Experiment>,
    criteria: Vec<Criterion>,
    /// the worktrees nested in the parent's checkout that its writes keep, by their paths from its root
    trees: Vec<String>,
    /// each experiment's parts that wait for its user there, with who wrote them
    unconfirmed: std::collections::HashMap<String, Vec<(String, String)>>,
    /// each experiment's way back to its parent's runs, which do not travel (reproduce::rerun_plan)
    plans: indexmap::IndexMap<String, Value>,
    /// what the parent's runs wrote, by path: the files they read their numbers from and kept, each in its results
    /// folder where it is in one; never the database a row was read from, which is no run's own
    outputs: Vec<String>,
    /// what the parent's runs read, by path: never taken out of the code's branch
    reads: Vec<String>,
    /// every file the parent's runs name, by path
    run_files: Vec<String>,
}

/// The parent's record in a verified bundle. The folders it was fetched and unpacked into are gone when this returns.
fn read_parent(source: &str) -> Res<Parent> {
    // declared before the parent's ledger, so the unpacked folder goes after it
    let (record, man, _unpacked) = bundle_record(source)?;
    let parent = Ledger::open(Some(&record))?;
    // the index sets aside what only a newer rb reads, so a clone made from it would leave that part behind unsaid
    if let Some(first) = parent.newer()?.first() {
        return Err(RBError::msg_fix(
            "E_STATE_NEWER",
            format!(
                "{} holds a record with a part only a newer rb reads ({}); a clone would leave it behind, so this rb clones nothing until it is upgraded.",
                crate::http::said(source),
                first.get("why").map(py_str).unwrap_or_default()
            ),
            "Upgrade: `pip install --upgrade rabbit-brain`, then clone the bundle again.",
        ));
    }
    let runs = parent.all::<Run>()?;
    let mut reads = reads_of(&runs, &parent.all::<Resource>()?);
    // a file the record's checks read (a setting's, a criterion's or an analysis's source) stays, so they read it here
    reads.extend(cited_paths(&parent)?);
    reads.sort();
    reads.dedup();
    Ok(Parent {
        man,
        proj: parent.project()?,
        questions: parent.live::<Question>()?,
        metrics: parent.live::<Metric>()?,
        resources: parent.live::<Resource>()?,
        experiments: parent.live::<Experiment>()?,
        criteria: parent.live::<Criterion>()?,
        trees: crate::view::page_trees_of(&parent),
        unconfirmed: parent.designs()?.iter().map(|(id, d)| (id.clone(), d.unconfirmed())).filter(|(_, u)| !u.is_empty()).collect(),
        plans: crate::reproduce::rerun_plan(&parent)?,
        outputs: outputs_of(&runs, &reads),
        run_files: runs.iter().flat_map(|r| r.files.iter().map(|f| plain_path(&f.path))).collect(),
        reads,
    })
}

/// Verify the bundle (a directory or a zip), then found a new project at `dest` carrying the parent's structure.
pub fn clone(source: &str, dest: Option<&Path>, code: Option<&str>) -> Res<Dict> {
    // a signal that would end rb while its folders are in the temporary directory ends it once they are gone
    let endings = crate::sys::catch_endings();
    let read = read_parent(source);
    drop(endings);
    if crate::sys::interrupted() {
        crate::sys::end_interrupted();
    }
    let Parent { man, proj, questions, metrics, resources, experiments, criteria, trees, unconfirmed, plans, outputs, run_files, reads } = read?;
    // the parts of a design that waited for its user there, with who wrote them, said on the line that writes their
    // file here: a design a session wrote waits here too (nothing the clone does not carry, a gate or a freeze)
    let waited = |exp: &str, file: Option<&str>| -> Dict {
        unconfirmed
            .get(exp)
            .into_iter()
            .flatten()
            .filter(|(k, _)| {
                let (kind, rest) = k.split_once(':').unwrap_or((k.as_str(), ""));
                let crit = matches!(kind, "rule" | "role" | "link").then(|| rest.split(':').next().unwrap_or(""));
                !matches!(kind, "gate" | "gate_words" | "freeze" | "freeze_why" | "amendment" | "amendment_why") && crit == file
            })
            .map(|(k, by)| (k.clone(), Value::Str(by.clone())))
            .collect()
    };
    let cwd = std::env::current_dir().unwrap_or_default();
    let dest = crate::sources::resolve_path(&match dest {
        Some(d) => d.to_path_buf(),
        None => cwd.join(&proj.id),
    });
    let probe = if dest.exists() { dest.clone() } else { dest.parent().map(|p| p.to_path_buf()).unwrap_or_default() };
    if let Some(existing) = find_root(Some(&probe)) {
        return Err(RBError::msg("E_PROJECT_EXISTS", format!("{} already holds a project; a clone starts its own.", crate::sys::slashed(&existing.join(DIR)))));
    }
    if dest.exists() && std::fs::read_dir(&dest).map_or(false, |mut rd| rd.next().is_some()) {
        return Err(RBError::msg_fix(
            "E_WRITE_FAILED",
            format!("{} exists and is not empty; rb will not clone into it.", crate::sys::slashed(&dest)),
            "Pick another destination: rb clone <bundle> <directory>.",
        ));
    }
    // where it came from, without what the address carried for the download alone (credentials, a signed query)
    let url = crate::http::said(source);
    // the code the runs need, where the bundle names it or the caller gives it: the record lands in a checkout of it
    let named = man.get("repo").filter(|r| r.truthy());
    let address = code.map(str::to_string).or_else(|| named.and_then(|r| r.get("url")).map(py_str)).filter(|a| !a.trim().is_empty());
    let wanted = named.and_then(|r| r.get("commit")).map(py_str).filter(|c| !c.is_empty());
    // the record's commit takes the place of the parent's record: with no identity of the user's own, git would
    // read the parent's committed log against this record's, so the code is not got at all
    let got = address.as_deref().map(|a| if has_identity() { get_code(&dest, a, wanted.as_deref(), &outputs, &run_files, &reads) } else { Err(NO_IDENTITY.to_string()) });
    let (via, agent) = crate::ledger::session(None, None);
    let led = Ledger::new(&dest, &via, agent);
    // first: who you are is read from git there, as on every later write
    std::fs::create_dir_all(&led.dir).map_err(|e| RBError::could_not_write(&e, &led.dir))?;
    let who = led.actor()?;
    let at = crate::pytime::now();
    let version = [man.get("written"), man.get("rb_version")].into_iter().flatten().find(|v| v.truthy()).map(py_str).unwrap_or_default();
    let origin = crate::dict! {"url" => url.clone(), "version" => version, "cloned_at" => at.clone()};
    let notes: Vec<Value> = proj.notes.iter().map(|n| Value::Dict(n.dump(false))).collect();
    let new: Project = build_with(
        crate::dict! {
            "id" => proj.id.clone(), "uid" => new_uid("prj"), "title" => proj.title.clone(), "created_by" => who.id.clone(), "created_at" => at.clone(),
            "via" => led.via.clone(), "parent" => Value::Dict(origin), "notes" => Value::List(notes)
        },
        Project::validate,
    )?;
    led.locked(|| {
        led.write(&led.dir.join(".gitignore"), GITIGNORE)?;
        led.write(&led.dir.join(".gitattributes"), crate::ledger::GITATTRIBUTES)?;
        let mut detail = crate::dict! {"title" => new.title.clone(), "parent_url" => url.clone()};
        // the code as the bundle or the caller named it, with the commit it was got at: what Rerun reads back
        let (commit, taken) = match &got {
            Some(Ok((full, taken))) => (Some(full.clone()), taken.clone()),
            _ => (wanted.clone(), Vec::new()),
        };
        if let Some(a) = &address {
            // whether the code was got here, and, where it was not, whether git failed at the address (then Rerun
            // never offers it again) or git had no identity to commit the record with (then the address stands)
            let failed = matches!(&got, Some(Err(why)) if why != NO_IDENTITY);
            detail.insert(
                "code".into(),
                crate::obj! {"url" => said_address(a), "commit" => commit.map(Value::Str).unwrap_or(Value::Null), "taken_out" => Value::list_of_str(taken),
                    "got" => matches!(&got, Some(Ok(_))), "failed" => failed},
            );
        }
        led.commit(&who, "clone", Obj::Project(new.clone()), detail)
    })?;
    let mine = |d: &mut Dict| {
        d.insert("created_by".into(), Value::Str(who.id.clone()));
        d.insert("created_at".into(), Value::Str(at.clone()));
        d.insert("via".into(), Value::Str(led.via.clone()));
    };
    let mut counts: Vec<(&str, i64)> = vec![("questions", 0), ("metrics", 0), ("resources", 0), ("experiments", 0), ("criteria", 0)];
    let bump = |counts: &mut Vec<(&str, i64)>, k: &str| {
        if let Some(c) = counts.iter_mut().find(|(n, _)| *n == k) {
            c.1 += 1;
        }
    };
    led.locked(|| {
        let qids: std::collections::HashSet<String> = questions.iter().map(|q| q.id.clone()).collect();
        for q in &questions {
            let parent_q = q.parent.clone().filter(|p| qids.contains(p));
            let mut d = crate::dict! {"id" => q.id.clone(), "text" => q.text.clone(), "parent" => parent_q};
            mine(&mut d);
            led.commit(&who, "clone", Obj::Question(build(d)?), Dict::new())?;
            bump(&mut counts, "questions");
        }
        for m in &metrics {
            let mut d = crate::dict! {"id" => m.id.clone(), "unit" => m.unit.clone(), "direction" => m.direction.clone(),
                "aliases" => Value::list_of_str(m.aliases.clone()), "description" => m.description.clone()};
            mine(&mut d);
            led.commit(&who, "clone", Obj::Metric(build(d)?), Dict::new())?;
            bump(&mut counts, "metrics");
        }
        // another machine's absolute path, in either system's form, is left behind, and so is a file in a worktree of
        // the parent's checkout: only that machine had either
        let in_tree = |x: &str| trees.iter().any(|t| x.strip_prefix(t.as_str()).map_or(false, |r| r.starts_with('/')));
        let foreign = |x: &str| Path::new(x).is_absolute() || x.starts_with('/') || x.get(1..3) == Some(":/") || in_tree(x);
        for r in &resources {
            let kept: Vec<Value> = r.locations.iter().filter(|l| !(l.provider == "local" && foreign(&l.r#ref))).map(|l| Value::Dict(l.dump(false))).collect();
            let mut d = crate::dict! {
                "id" => r.id.clone(), "uid" => r.uid.clone(), "type" => r.r#type.clone(), "name" => r.name.clone(), "version" => r.version.clone(),
                "subset" => r.subset.clone(), "sha256" => r.sha256.clone(), "digest" => r.digest.clone(),
                "acquisition" => r.acquisition.as_ref().map(|a| Value::Dict(a.dump(false))).unwrap_or(Value::Null),
                "locations" => Value::List(kept), "note" => r.note.clone()
            };
            mine(&mut d);
            led.commit(&who, "clone", Obj::Resource(build(d)?), crate::dict! {"parent_uid" => r.uid.clone()})?;
            bump(&mut counts, "resources");
        }
        let mut cloned: std::collections::HashSet<String> = std::collections::HashSet::new();
        for e in &experiments {
            let mut variants: Vec<Value> = Vec::new();
            for v in e.variants.iter().filter(|v| v.retracted.is_none()) {
                let mut d = crate::dict! {"name" => v.name.clone(), "role" => v.role.clone(), "note" => v.note.clone()};
                mine(&mut d);
                d.insert(
                    "settings".into(),
                    Value::List(v.settings.iter().filter(|s| s.retracted.is_none()).map(|s| inherit(s, &who.id, &at, &led.via)).collect()),
                );
                let built: Variant = build_with(d, Variant::validate)?;
                variants.push(Value::Dict(built.dump(false)));
            }
            let target = e.uid.clone();
            let rel: Relation = build_with(
                crate::dict! {"kind" => "reproduces", "target" => target.clone(), "note" => format!("cloned from {url}"), "by" => who.id.clone(), "at" => at.clone()},
                Relation::validate,
            )?;
            let mut relations: Vec<Value> = e.relations.iter().map(|r| Value::Dict(r.dump(false))).collect();
            relations.push(Value::Dict(rel.dump(false)));
            let mut d = crate::dict! {
                "id" => e.id.clone(), "uid" => new_uid("exp"), "title" => e.title.clone(),
                "questions" => Value::list_of_str(e.questions.iter().filter(|q| qids.contains(*q)).cloned()),
                "purpose" => e.purpose.clone(), "hypothesis" => e.hypothesis.clone(), "why" => e.why.clone(),
                "varies" => Value::list_of_str(e.varies.clone()), "variants" => Value::List(variants),
                "settings" => Value::List(e.settings.iter().filter(|s| s.retracted.is_none()).map(|s| inherit(s, &who.id, &at, &led.via)).collect()),
                "notes" => Value::List(e.notes.iter().map(|n| Value::Dict(n.dump(false))).collect()),
                "relations" => Value::List(relations)
            };
            mine(&mut d);
            let mut detail = crate::dict! {"parent" => target};
            if let Some(plan) = plans.get(&e.id) {
                detail.insert("rerun".into(), plan.clone());
            }
            let waits = waited(&e.id, None);
            if !waits.is_empty() {
                detail.insert("unconfirmed_parts".into(), Value::Dict(waits));
            }
            led.commit(&who, "clone", Obj::Experiment(build(d)?), detail)?;
            cloned.insert(e.id.clone());
            bump(&mut counts, "experiments");
        }
        for c in &criteria {
            if !cloned.contains(&c.experiment) {
                continue;
            }
            let note: String = [c.note.clone(), if c.origin == "cited" { "[cited in the parent project]".to_string() } else { String::new() }]
                .into_iter()
                .filter(|x| !x.is_empty())
                .collect::<Vec<_>>()
                .join(" ")
                .chars()
                .take(1000)
                .collect();
            let mut d = crate::dict! {
                "id" => c.id.clone(), "experiment" => c.experiment.clone(), "statement" => c.statement.clone(), "metric" => c.metric.clone(),
                "subject" => c.subject.clone(), "reference" => c.reference.clone(), "estimand" => c.estimand.clone(), "comparator" => c.comparator.clone(),
                "target" => c.target, "tolerance" => c.tolerance, "margin" => c.margin, "over" => c.over.clone(), "role" => c.role.clone(),
                "min_n" => c.min_n, "noise" => c.noise, "origin" => "inherited", "note" => note,
                "question_links" => Value::List(c.question_links.iter().filter(|link| link.retracted.is_none() && qids.contains(&link.question)).map(|link| Value::Dict(link.dump(false))).collect()),
                "relations" => Value::List(c.relations.iter().filter(|r| r.retracted.is_none()).map(|r| Value::Dict(r.dump(false))).collect())
            };
            let raw = c.dump(false);
            for key in ["where", "subject_where", "reference_where"] {
                if let Some(value) = raw.get(key) { d.insert(key.into(), value.clone()); }
            }
            mine(&mut d);
            let crit: Criterion = build(d)?;
            let mut detail = crate::dict! {"rule" => crit.rule(), "experiment" => c.experiment.clone()};
            let waits = waited(&c.experiment, Some(c.id.as_str()));
            if !waits.is_empty() {
                detail.insert("unconfirmed_parts".into(), Value::Dict(waits));
            }
            led.commit(&who, "clone", Obj::Criterion(crit), detail)?;
            bump(&mut counts, "criteria");
        }
        Ok(())
    })?;
    let git = match &got {
        Some(Ok((commit, _))) => commit_record(&dest, &proj.id, &url, commit),
        _ => make_repo(&dest, &proj.id, &url),
    };
    let code_out = match (&address, &got) {
        (Some(a), Some(Ok((commit, taken)))) => crate::obj! {
            "url" => said_address(a), "commit" => commit.clone(), "got" => true, "branch" => BRANCH, "why" => "", "git_said" => "", "taken_out" => Value::list_of_str(taken.clone())
        },
        (Some(a), Some(Err(why))) => {
            let (mut why, git_said) = said_apart(why);
            // the bundle's own address, which its export saw does not hold the code's commit: the bundle's words
            let not_there = code.is_none() && named.and_then(|r| r.get("at_origin")).map_or(false, |v| matches!(v, Value::Bool(false)));
            if let (true, Some(c)) = (not_there, wanted.as_deref()) {
                why = format!("the bundle says its code's commit {} is not at {}", c.chars().take(12).collect::<String>(), said_address(a));
            }
            crate::obj! {
                "url" => said_address(a), "commit" => wanted.clone().map(Value::Str).unwrap_or(Value::Null), "got" => false, "branch" => Value::Null, "why" => why,
                "git_said" => git_said, "taken_out" => Value::List(Vec::new())
            }
        }
        _ => Value::Null,
    };
    let mut out = crate::dict! {
        "code" => code_out,
        "path" => crate::sys::slashed(&dest), "project" => proj.id.clone(), "title" => proj.title.clone(), "git" => git,
        "parent" => crate::obj! {"url" => url.clone(), "version" => man.get("written").cloned().unwrap_or(Value::Null), "cloned_at" => at.clone(), "uid" => proj.uid.clone()},
        "repo" => man.get("repo").cloned().unwrap_or(Value::Null)
    };
    for (k, n) in counts {
        out.insert(k.to_string(), Value::Int(n));
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    //! The INT8 ResNet-50 on ImageNet example, written by human:ana.
    use super::*;

    #[test]
    fn a_path_a_rerun_may_need_is_kept_however_it_is_spelled() {
        let res: Resource = crate::ledger::build(crate::dict! {
            "id" => "calib", "uid" => crate::record::new_uid("res"), "type" => "dataset", "name" => "INT8 calibration images",
            "created_by" => "human:ana", "created_at" => "2026-10-20T09:00:00+00:00",
            "locations" => Value::List(vec![crate::obj! {"provider" => "local", "ref" => "./results//calib/images.json", "by" => "human:ana", "at" => "2026-10-20T09:00:00+00:00"}])
        })
        .unwrap();
        let reads = reads_of(&[], &[res]);
        assert_eq!(reads, vec!["results/calib/images.json".to_string()]);
        assert!(holds_a_read(&reads, "results/calib/") && !holds_a_read(&reads, "results/r001/"));
        assert_eq!(plain_path("./results/r001/"), "results/r001/");
        // code is never the parent's results, by its suffix
        assert!(is_code("results/tools/summarise.py") && is_code("results/plot.IPYNB") && !is_code("results/s1/metrics.json") && !is_code("results/.py"));
    }

    #[test]
    fn git_is_quoted_by_its_error_never_a_fragment_of_its_advice() {
        // git for Windows on a path that is no repository, CRLF and all; ssh that cannot connect, anywhere
        let windows = "fatal: 'C:\\Users\\ana\\no-such-repo.git' does not appear to be a git repository\r\nfatal: Could not read from remote repository.\r\n\r\nPlease make sure you have the correct access rights\r\nand the repository exists.\r\n";
        assert_eq!(git_message(windows), "'C:\\Users\\ana\\no-such-repo.git' does not appear to be a git repository. Could not read from remote repository.");
        let ssh = "ssh: connect to host example.org port 22: Connection refused\nfatal: Could not read from remote repository.\n\nPlease make sure you have the correct access rights\nand the repository exists.\n";
        assert_eq!(git_message(ssh), "ssh: connect to host example.org port 22: Connection refused. Could not read from remote repository.");
        assert_eq!(git_message("fatal: repository '/srv/no-such-repo.git' does not exist\n"), "repository '/srv/no-such-repo.git' does not exist.");
        assert_eq!(git_message(""), "git failed");
    }

    #[test]
    fn an_address_is_kept_as_a_reader_can_use_it() {
        assert_eq!(said_address("https://ana:secret@example.org/ana/int8.git?token=1"), "https://example.org/ana/int8.git");
        assert_eq!(said_address("git@github.com:ana/int8.git"), "git@github.com:ana/int8.git");
        // an @ past the first / after credentials or a `:`: no address is said, in rb's words and in git's alike
        assert_eq!(said_address("https://ana:pa/ss@example.org/ana/int8.git"), crate::http::UNREADABLE);
        let raw = "https://ana:pa/ss@example.org/ana/int8.git";
        let told = without_address(&format!("repository '{raw}' not found."), raw);
        assert_eq!(told, "repository 'an address rb cannot read safely' not found.");
        // git's own form of the address, its credentials taken out by git, is said as rb says it too
        let signed = "https://ana:secret@example.org/ana/int8.git?sig=s3cret";
        let told = without_address("unable to access 'https://example.org/ana/int8.git?sig=s3cret/': 403", signed);
        assert_eq!(told, "unable to access 'https://example.org/ana/int8.git/': 403");
        let odd = "https://ana:secret@example.org/ana/int8.git?next=a@b";
        let told = without_address("unable to access 'https://example.org/ana/int8.git?next=a@b/'", odd);
        assert_eq!(told, "unable to access 'an address rb cannot read safely/'");
        // git takes out only what comes before the first @: a user's own @ leaves the password in git's form
        let user_at = "https://jo@x.com:tok@example.org/r";
        let told = without_address("unable to access 'https://x.com:tok@example.org/r/': bad URL", user_at);
        assert_eq!(told, "unable to access 'https://example.org/r/': bad URL");
        // a ? before an @: no address, in rb's words and in git's
        let query = "https://ana:pw@example.org?sig=a@b";
        assert_eq!(said_address(query), crate::http::UNREADABLE);
        let told = without_address("unable to access 'https://example.org?sig=a@b/'", query);
        assert_eq!(told, "unable to access 'an address rb cannot read safely/'");
        assert!(!told.contains("pa/ss") && !told.contains("ana:"));
        // an address is said in place only where it ends (a quote, a space, a / or the end): a longer address that
        // starts with the same text is kept whole, never rewritten in part
        let short = "https://ana:secret@example.org/ana/int8";
        let told = without_address("unable to access 'https://example.org/ana/int8-v2.git/': 404, see https://example.org/ana/int8", short);
        assert_eq!(told, "unable to access 'https://example.org/ana/int8-v2.git/': 404, see https://example.org/ana/int8");
        let told = without_address("repository 'https://ana:secret@example.org/ana/int8' not found; tried https://ana:secret@example.org/ana/int8 and https://ana:secret@example.org/ana/int8.git", short);
        assert_eq!(told, "repository 'https://example.org/ana/int8' not found; tried https://example.org/ana/int8 and https://example.org/ana/int8.git");
        // so with a password holding a /, and in git's own form of a user's own @
        let told = without_address("unable to access 'https://ana:pa/ss@example.org/ana/int8.git'", "https://ana:pa/ss@example.org/ana/int8");
        assert_eq!(told, "unable to access 'an address rb cannot read safely.git'");
        let told = without_address("could not read https://ana:pa/ss@example.org/r?token=SECRET.", "https://ana:pa/ss@example.org/r?token=SECRET");
        assert_eq!(told, "could not read an address rb cannot read safely.");
        // a bare @ hides nothing, so nothing is cut for it
        assert_eq!(without_address("see @foo and (@bar)", "https://@host/r"), "see @foo and (@bar)");
        let told = without_address("bad URL https://x.com:tok@example.org/r2", user_at);
        assert_eq!(told, "bad URL https://example.org/r2");
        // a token with a / or a signed query says no more in an address that does not end there
        assert_eq!(without_address("could not read https://tok/en@host/r.", "https://tok/en@host/r"), "could not read an address rb cannot read safely.");
        assert_eq!(without_address("tried https://tok/en@host/r.git and failed.", "https://tok/en@host/r"), "tried an address rb cannot read safely.git and failed.");
        // and where rb says no address, git's message names no host either: curl's host can be part of a token
        let told = without_address(&git_message("fatal: unable to access 'https://tok/en@host/r/': Could not resolve host: tok\n"), "https://tok/en@host/r");
        assert_eq!(told, "unable to access 'an address rb cannot read safely/': Could not resolve host: an address rb cannot read safely.");
        let told = without_address("Failed to connect to tok port 443 after 3 ms: tok.en, toked and stok stay", "https://tok/en@host/r");
        assert_eq!(told, "Failed to connect to an address rb cannot read safely port 443 after 3 ms: tok.en, toked and stok stay");
        // as git 2 says it for https://lab.example?next=a@b
        let told = without_address(&git_message("fatal: unable to access 'https://b/': Could not resolve host: lab.example\n"), "https://lab.example?next=a@b");
        assert_eq!(told, "unable to access 'an address rb cannot read safely/': Could not resolve host: an address rb cannot read safely.");
        // git names user:pass as a host and its port for git://; curl decodes escapes, prints a name beyond ASCII as
        // punycode and an IPv6 host without brackets; ssh says a host in lower case
        let told = without_address(&git_message("fatal: unable to look up deploy:s3cr (port 9418) (Name or service not known)\n"), "git://deploy:s3cr/et@127.0.0.1:9/r");
        assert_eq!(told, "unable to look up an address rb cannot read safely (port 9418) (Name or service not known).");
        assert_eq!(without_address("Could not resolve host: s3cret", "https://s3cr%65t/x@host/r"), "Could not resolve host: an address rb cannot read safely");
        assert_eq!(without_address("Could not resolve host: xn--tkn-cma7d", "https://tökén/x@host/r"), "Could not resolve host: an address rb cannot read safely");
        assert_eq!(without_address("Failed to connect to ::1 port 9 after 0 ms", "https://[::1]:9/r?x=a@b"), "Failed to connect to an address rb cannot read safely port 9 after 0 ms");
        assert_eq!(without_address("ssh: Could not resolve hostname tok: Name or service not known", "ssh://git@TOK/en@host/r"), "ssh: Could not resolve hostname an address rb cannot read safely: Name or service not known");
        // git:// and ssh print the user's part before the host; curl writes a lone % back as %25 and skips an extra /
        let told = without_address("unable to look up u:p@tok (port 9418) (Name or service not known).", "git://u:p@tok/en@host/r");
        assert_eq!(told, "unable to look up an address rb cannot read safely (port 9418) (Name or service not known).");
        let told = without_address("unable to look up u:p@127.0.0.1:9 (port 9418).", "git://u:p@127.0.0.1:9/x@h/r");
        assert_eq!(told, "unable to look up an address rb cannot read safely (port 9418).");
        let told = without_address("deploy:s3cr@tok: Permission denied (publickey).", "ssh://deploy:s3cr@tok/x?y=a@b");
        assert_eq!(told, "an address rb cannot read safely: Permission denied (publickey).");
        assert_eq!(without_address("Could not resolve host: to%25k.", "https://to%k/x@host/r"), "Could not resolve host: an address rb cannot read safely.");
        assert_eq!(without_address("Could not resolve host: %25.", "https://%/x@host/r"), "Could not resolve host: an address rb cannot read safely.");
        assert_eq!(without_address("Could not resolve host: tok.", "https:///tok/en@host/r"), "Could not resolve host: an address rb cannot read safely.");
        assert_eq!(without_address("Could not resolve host: secrethost.", "https:///user:pass@secrethost/r"), "Could not resolve host: an address rb cannot read safely.");
        // curl maps letters beyond ASCII to ASCII ones, and writes a lone % back beside an escape it decodes: whatever
        // stands where it names the host is said as the phrase
        assert_eq!(without_address("Could not resolve host: token.", "https://ｔｏｋｅｎ/x@h/r"), "Could not resolve host: an address rb cannot read safely.");
        assert_eq!(without_address("Could not resolve host: tok.en.", "https://tok。en/x@h/r"), "Could not resolve host: an address rb cannot read safely.");
        assert_eq!(without_address("Could not resolve host: to%25kA.", "https://to%k%41/x@h/r"), "Could not resolve host: an address rb cannot read safely.");
        assert_eq!(without_address("Failed to connect to seCret port 443: Connection refused", "https://x@h/y?a=b@c"), "Failed to connect to an address rb cannot read safely port 443: Connection refused");
        assert_eq!(without_address("ssh: Could not resolve hostname s3cr: Name or service not known", "ssh://x/y@z/r"), "ssh: Could not resolve hostname an address rb cannot read safely: Name or service not known");
        assert_eq!(without_address("Connection closed by s3cr port 22", "ssh://x/y@z/r"), "Connection closed by an address rb cannot read safely port 22");
        assert_eq!(without_address("ssh: connect to host s3cr port 22: Connection refused", "ssh://x/y@z/r"), "ssh: connect to host an address rb cannot read safely port 22: Connection refused");
        assert_eq!(without_address("Failed to connect to s3cr port 9: Couldn't connect to server.", "https://x/y@z/r"), "Failed to connect to an address rb cannot read safely port 9: Couldn't connect to server.");
        // an address rb says keeps git's host
        assert_eq!(without_address("Could not resolve host: example.org", "https://ana:secret@example.org/r"), "Could not resolve host: example.org");
        assert_eq!(without_address("could not fetch https://host/r.git?token=SECRET.", "https://host/r.git?token=SECRET"), "could not fetch https://host/r.git.");
        assert_eq!(without_address("could not fetch https://ana:pw@host/r.git?token=SECRET.", "https://ana:pw@host/r.git?token=SECRET"), "could not fetch https://host/r.git.");
        // git:// and ssh name the host with no scheme before it
        assert_eq!(without_address("unable to look up u:s3cr3t@127.0.0.1:9 (port 9418)", "git://u:s3cr3t@127.0.0.1:9/r"), "unable to look up 127.0.0.1:9 (port 9418)");
        assert_eq!(without_address("git:pa/ss@host: Permission denied", "ssh://git:pa/ss@host/r.git"), "host: Permission denied");
        let local = if cfg!(windows) { "C:\\Users\\ana\\int8.git" } else { "/home/ana/int8.git" };
        assert_eq!(said_address(local), if cfg!(windows) { "C:/Users/ana/int8.git" } else { "/home/ana/int8.git" });
    }

    #[test]
    fn where_rb_says_no_address_it_says_why_in_its_own_words_and_no_git_message() {
        // every address rb cannot read safely that the reviews of git's message probed takes that path
        let unreadable = [
            "git://deploy:s3cr/et@127.0.0.1:9/r", "https://s3cr%65t/x@host/r", "https://t%6Fk/en@host/r", "https://AB%43d/x@host/r",
            "https://tökén/x@host/r", "https://[::1]:9/r?x=a@b", "https://tok/en@host/r", "https://lab.example?next=a@b", "https://tok?en@host/r",
            "git://u:p@tok/en@host/r", "git://deploy:s3cr@tok/en@host/r", "git://u:p@127.0.0.1:9/x@h/r", "ssh://u:p@TOK/en@host/r",
            "ssh://deploy:s3cr@tok/x?y=a@b", "https://to%k/x@host/r", "https://to%zzk/x@host/r", "https://tok%/x@host/r", "https://%/x@host/r",
            "https:///tok/en@host/r", "https:///user:pass@secrethost/r", "https:///x@host/r?y=a@b", "https://tok。en/x@h/r",
            "https://ｔｏｋｅｎ/x@h/r", "https://to\u{ad}ken/x@h/r", "https://to%k%41/x@h/r", "https://to%41%k/x@h/r", "git://tok:12345/x@h/r",
        ];
        for a in unreadable {
            assert_eq!(said_address(a), crate::http::UNREADABLE, "{a}");
        }
        // and git, failing on one (nothing listens on port 9 here), is not quoted: no host, no port, no git words
        let dest = std::env::temp_dir().join(format!("rb-clone-unreadable-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dest);
        let why = get_code(&dest, "git://u:p@127.0.0.1:9/x@h/r", None, &[], &[], &[]).unwrap_err();
        assert_eq!(why, UNREADABLE_WHY);
        assert_eq!(said_apart(&why), (UNREADABLE_WHY.to_string(), String::new()));
        assert!(!dest.exists());
        // an address rb says still gets git's own message, after "git said:"
        let why = get_code(&dest, "git://127.0.0.1:9/r", None, &[], &[], &[]).unwrap_err();
        assert!(why.starts_with(GIT_SAID) && why.contains("127.0.0.1"), "{why}");
    }
}
