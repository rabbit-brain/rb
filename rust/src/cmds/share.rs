//! The commands that carry the record somewhere else: rb page, rb export (its page, and the record when asked) and rb clone.

use super::*;

type R = Res<i32>;

/// `dest.relative_to(Path.cwd())`, or `dest` when it is not under the current directory.
pub fn shown_path(dest: &Path) -> String {
    let cwd = std::env::current_dir().unwrap_or_default();
    match dest.strip_prefix(&cwd) {
        Ok(rel) => {
            let s = crate::sys::slashed(rel);
            if s.is_empty() {
                ".".into()
            } else {
                s
            }
        }
        Err(_) => crate::sys::slashed(dest),
    }
}

pub fn cmd_page(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let text = crate::shell::page(&led, crate::shell::Surface::Page, None)?;
    let dest = match s(ns, "out").filter(|o| !o.is_empty()) {
        Some(o) => crate::sources::resolve_path(Path::new(&o)),
        None => led.root.join("rb-page.html"),
    };
    // a file at --out that rb did not write is a research file: never written over unless asked (--replace)
    crate::owned::may_write_over(&led.root, &dest, flag(ns, "replace"), &crate::export::is_rb_page)?;
    let (before, made) = (crate::owned::sha256_of(&dest), crate::owned::missing_dirs(&dest));
    // --out may name a directory that is not there yet: the page is written where it was asked for
    if let Some(parent) = dest.parent().filter(|p| !p.as_os_str().is_empty()) {
        std::fs::create_dir_all(parent).map_err(|e| RBError::could_not_write(&e, &dest))?;
    }
    std::fs::write(&dest, text.as_bytes()).map_err(|e| RBError::could_not_write(&e, &dest))?;
    crate::owned::wrote(&led.root, crate::owned::scope_of(&led.root, &dest), &dest, "output", before.as_deref(), &made, Dict::new());
    out.say(format!("{} written: the project as one file, readable anywhere; send it to anyone.", shown_path(&dest)));
    out.say("It is the record as computed now; regenerate after new runs. If it ever disagrees with rb status, rb status is right.");
    out.data.insert("path".into(), Value::Str(crate::sys::slashed(&dest)));
    out.data.insert("bytes".into(), Value::Int(text.len() as i64));
    Ok(0)
}

pub fn cmd_export(ns: &Namespace, out: &mut Out) -> R {
    let led = Ledger::open(None)?;
    let dest_arg = s(ns, "out").filter(|o| !o.is_empty());
    let with_record = flag(ns, "with_record");
    let res = crate::export::export(&led, dest_arg.as_deref().map(Path::new), flag(ns, "zip"), s(ns, "url").as_deref(), flag(ns, "no_repo"), with_record, flag(ns, "replace"))?;
    let rv = Value::Dict(res.clone());
    let dest = PathBuf::from(get_s(&res, "path"));
    let shown = shown_path(&dest);
    if with_record {
        out.say(format!("{shown} written: the whole record, the page, and a manifest hashing every file."));
        out.say("Readable without rb; rb status inside record/ reopens it. Regenerate after new runs.");
        out.say(format!("{} Anyone who can read the bundle can read all of it.", crate::export::record_carries(&led)));
    } else {
        out.say(format!("{shown} written: the project's page, and a manifest hashing it."));
        out.say("It is the record as computed now; regenerate after new runs. The record stays here: rb export --with-record carries it too, for rb clone.");
    }
    if let Some(r) = rv.get("repo").filter(|r| r.truthy()) {
        let commit = r.get("commit").filter(|c| c.truthy()).map(pyjson::py_str);
        out.say(
            format!("Code repository recorded: {}", r.get("url").map(pyjson::py_str).unwrap_or_default())
                + &commit.as_deref().map(|c| format!(" at {}", c.chars().take(10).collect::<String>())).unwrap_or_default()
                + ". Pass --no-repo to leave it out.",
        );
        let at = commit.as_deref().map(|c| c.chars().take(10).collect::<String>()).unwrap_or_default();
        // what the checkout changes of that commit, which a rerun does not get (export.rs's repo.changed)
        if let Some(Value::List(changed)) = r.get("changed").filter(|c| c.truthy()) {
            let paths: Vec<String> = changed.iter().take(10).map(pyjson::py_str).collect();
            let more = if changed.len() > 10 { format!(" and {} more", changed.len() - 10) } else { String::new() };
            let n = if changed.len() == 1 { "1 file".to_string() } else { format!("{} files", changed.len()) };
            if at.is_empty() {
                // no commit yet: nothing a rerun can get
                out.say(format!("This checkout has no commit yet: {n} not committed: {}{more}.", paths.join(", ")));
                out.say("A rerun gets no code until it is committed.");
            } else {
                out.say(format!("This checkout changes the code at {at}: {n} not committed: {}{more}.", paths.join(", ")));
                out.say(if changed.len() == 1 {
                    "A rerun gets the commit without it; commit it first to share it."
                } else {
                    "A rerun gets the commit without them; commit them first to share them."
                });
            }
        }
        // origin, as this clone last saw it, lacks the commit (export.rs's repo.at_origin, written only when false)
        if matches!(r.get("at_origin"), Some(Value::Bool(false))) {
            let url = r.get("url").map(pyjson::py_str).unwrap_or_default();
            out.say(format!("origin, as this clone last saw it, does not hold commit {at}: a rerun cannot get the code from {url} until it is pushed there."));
        }
    }
    // origin is no address a reader can use (a path of this machine, file://, ssh): the export names none, once
    if let Some(line) = rv.get("repo_unnamed").filter(|l| l.truthy()) {
        out.say(pyjson::py_str(line));
    }
    if let Some(Value::List(missing)) = rv.get("sources_missing").filter(|m| m.truthy()) {
        out.say(format!("Cited sources no longer on disk, so not included: {}.", missing.iter().map(pyjson::py_str).collect::<Vec<_>>().join(", ")));
    }
    if let Some(Value::List(kept)) = rv.get("sources_at_commit").filter(|m| m.truthy()) {
        let named: Vec<String> =
            kept.iter().map(|k| format!("{} at {}", get_s(&dict_of(k), "path"), get_s(&dict_of(k), "commit").chars().take(10).collect::<String>())).collect();
        out.say(format!("Sources read at a commit, included as they stood there: {}.", named.join(", ")));
    }
    if let Some(url) = rv.get("url").filter(|u| u.truthy()).map(pyjson::py_str) {
        out.say(format!("Publish the directory at the address you gave; page.html is the reading surface and anyone reproduces it with: rb clone {url}"));
    }
    if let Some(Value::List(left)) = rv.get("resources_not_included").filter(|m| m.truthy()) {
        let parts: Vec<String> = left
            .iter()
            .map(|r| {
                let (state, here, how) = (get_s(&dict_of(r), "state"), get_s(&dict_of(r), "here"), get_s(&dict_of(r), "how"));
                format!("{} (", get_s(&dict_of(r), "id"))
                    + &if here == state { state.clone() } else { format!("{here} here; in the bundle {state}") }
                    + &if how.is_empty() { String::new() } else { format!(", {how}") }
                    + ")"
            })
            .collect();
        out.say(format!("Not in this bundle, so a reader cannot rerun with them from it alone: {}.", parts.join("; ")));
    }
    let published = rv.get("url").map_or(false, |u| u.truthy());
    for (k, v) in res {
        out.data.insert(k, v);
    }
    if with_record && !published {
        out.next = vec![format!("rb clone {shown} <dest>"), "rb export --with-record --url <public address>   (make it publishable)".into()];
    }
    Ok(0)
}

pub fn cmd_clone(ns: &Namespace, out: &mut Out) -> R {
    let source = s_or(ns, "source");
    // an address is fetched as it is; anything else is a path, as pathlib writes it
    let source = if source.starts_with("http://") || source.starts_with("https://") { source } else { pathlib_str(&source) };
    let dest_arg = s(ns, "dest").filter(|d| !d.is_empty());
    let code = s(ns, "code").filter(|c| !c.trim().is_empty());
    let res = crate::clone::clone(&source, dest_arg.as_deref().map(Path::new), code.as_deref())?;
    let rv = Value::Dict(res.clone());
    let dest = PathBuf::from(get_s(&res, "path"));
    let shown = shown_path(&dest);
    // a count with its noun, one or many
    let n = |k: &str, one: &str| {
        let c = rv.get(k).map(pyjson::py_str).unwrap_or_else(|| "0".into());
        format!("{c} {}", if c == "1" { one } else { k })
    };
    out.say(format!(
        "{shown}: \"{}\" cloned: {}, {}, {}, {}. Everything arrived inherited; nothing counts until it is reproduced here.",
        get_s(&res, "title"),
        n("experiments", "experiment"),
        n("criteria", "criterion"),
        n("metrics", "metric"),
        n("resources", "resource")
    ));
    out.say(format!("It is {}.", get_s(&res, "git")));
    out.say("The parent's runs, analyses and decisions stay in the parent's record. Record runs made in this repository, and each inherited criterion reads them like any finding: reproduced or not reproduced.");
    // the code the runs need: here, at its commit, or why not and the command that gets it
    let again = format!("rb clone {} <new folder> --code <address of the code>", crate::cli::shlex_quote(&crate::http::shareable(&source)));
    match rv.get("code").filter(|c| c.truthy()) {
        Some(c) if c.get("got").map_or(false, Value::truthy) => {
            let commit = c.get("commit").map(pyjson::py_str).unwrap_or_default();
            let taken = c.get("taken_out").and_then(Value::as_list).map_or(0, Vec::len);
            // "it" for the record alone, "them" for the record and the parent's results: what its runs wrote, and every
            // results folder the commit holds that no recorded run named (P's clone-fix5)
            let (what, them) = match taken {
                0 => ("The parent's record is taken out of this branch".to_string(), "it"),
                1 => ("The parent's record, and the one file or folder of its results, are taken out of this branch".to_string(), "them"),
                n => (format!("The parent's record, and the {n} files and folders of its results, are taken out of this branch"), "them"),
            };
            out.say(format!(
                "The code: {} at {}, checked out here. {what}; git keeps {them} at that commit, and the record here is this line of work's own.",
                c.get("url").map(pyjson::py_str).unwrap_or_default(),
                commit.chars().take(12).collect::<String>()
            ));
        }
        // rb's words first; where git failed, git's own message after "git said:"
        Some(c) => {
            let url = c.get("url").map(pyjson::py_str).unwrap_or_default();
            let said = c.get("git_said").map(pyjson::py_str).unwrap_or_default();
            let why = c.get("why").map(pyjson::py_str).unwrap_or_default();
            if said.is_empty() {
                out.say(format!("rb clone could not get the code from {url}: {}.", why.trim_end_matches('.')));
            } else {
                out.say(format!("rb clone could not get the code from {url}. git said: {}", said.trim_end()));
            }
            out.say(format!("With access, or a copy you can reach: {again}"));
        }
        None => out.say(format!("The runs need the code; this bundle doesn't say where it is. When you have it: {again}")),
    }
    for (k, v) in res {
        out.data.insert(k, v);
    }
    // a published page names the parent only at a web address
    let names = if crate::http::is_address(&source) { "; its page names the parent" } else { "" };
    out.next = vec![
        format!("cd {shown} && rb context   (the parent, the code, each resource and the first command to rerun)"),
        "rb env check   (does this machine match the runs that made it?)".into(),
        format!("rb export --with-record --url <your address>   (publish your fork{names})"),
    ];
    Ok(0)
}

pub fn cmd_login(ns: &Namespace, out: &mut Out) -> R {
    crate::hub::cmd_login(s(ns, "hub"), out)
}

pub fn cmd_logout(ns: &Namespace, out: &mut Out) -> R {
    crate::hub::cmd_logout(s(ns, "hub"), out)
}

pub fn cmd_sync(ns: &Namespace, out: &mut Out) -> R {
    if flag(ns, "watch") {
        return crate::hub::cmd_sync_watch(s(ns, "hub"), flag(ns, "with_record"), out);
    }
    crate::hub::cmd_sync(s(ns, "hub"), flag(ns, "with_record"), out)
}
