//! The rb skill: the instruction package a coding agent loads so it works under rb's evidence discipline. `rb skill`
//! prints it; `rb skill add` installs it where the user's agent looks.

use crate::argparse::Namespace;
use crate::cli::Out;
use crate::errors::{RBError, Res};
use crate::pyjson::{self, Dumps};
use crate::value::Value;
use std::path::{Path, PathBuf};

pub const BEGIN: &str = "<!-- rabbit-brain skill: begin (installed by `rb skill add`; rerun it to update) -->";
pub const END: &str = "<!-- rabbit-brain skill: end -->";
pub const SKILL_MD: &str = include_str!("../../SKILL.md");
pub const AGENTS_MD: &str = include_str!("../../AGENTS.md");
/// The start protocol: `rb docs --start` and rb://start.
pub const START_MD: &str = include_str!("assets/start.md");
/// The one line a researcher pastes into an agent's chat.
pub const PASTE_LINE: &str = "Use Rabbit Brain to track this: read rb context, then record where the work stands.";

pub fn skill_text() -> String {
    SKILL_MD.to_string()
}

/// The YAML frontmatter's description, and the body after the frontmatter.
fn split(text: &str) -> (String, String) {
    let mut description = "Evidence discipline for AI-assisted research with Rabbit Brain (rb).".to_string();
    let mut body = text.to_string();
    if let Some(rest) = text.strip_prefix("---\n") {
        if let Some(closing) = rest.find("\n---\n") {
            for line in crate::ledger::py_splitlines(&rest[..closing]) {
                if let Some(d) = line.strip_prefix("description:") {
                    description = crate::rbyaml::py_strip(d).to_string();
                }
            }
            body = rest[closing + "\n---\n".len()..].trim_start_matches('\n').to_string();
        }
    }
    (description, body)
}

/// Whether rb may change an instruction file that is there before it: asked once in the user's own terminal (`Ask`), said
/// up front (`--yes`), or not (no terminal, an agent session, --json): then rb makes only what is not there, and says
/// what to add to each file that is, and the command that adds it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Consent {
    Ask,
    Yes,
    No,
}

/// The consent this command line gives: `--yes`, else a question in the user's own terminal (standard input and output
/// a terminal, no agent session, no --json), else none. An agent session gives none, `--yes` or not: changing a file
/// that was there is the user's to agree to.
pub fn consent(yes: bool, json: bool) -> Consent {
    if session_runtime().is_some() {
        Consent::No
    } else if yes {
        Consent::Yes
    } else if !json && !crate::cli::capturing() && crate::cli::isatty(0) && crate::cli::isatty(1) && crate::actor::agent_session_sign().is_none() {
        Consent::Ask
    } else {
        Consent::No
    }
}

/// One file rb would write for an agent: made new (or rb's own file again), a file that is there changed by a splice
/// that keeps its bytes (`insert`: where rb's part goes), or a file that is there replaced whole (`old`: what it holds,
/// which the ledger keeps so rb remove puts it back).
#[derive(Debug, Clone)]
pub struct Install {
    pub path: PathBuf,
    /// created, section, mcp_entry or replaced
    pub how: &'static str,
    pub content: String,
    pub insert: Option<crate::splice::Insert>,
    pub old: Option<String>,
    /// what the ledger line keeps besides: rb's section and the bytes it took the place of, and what rb's own earlier
    /// write here kept of what was there before rb
    pub carry: crate::value::Dict,
}

impl Install {
    fn new(path: PathBuf, how: &'static str, content: String) -> Install {
        Install { path, how, content, insert: None, old: None, carry: crate::value::Dict::new() }
    }

    /// Whether it changes a file someone else made: what rb asks before.
    pub fn edits(&self) -> bool {
        self.insert.is_some() || self.old.is_some()
    }

    /// The lines of the section between rb's markers that this edit takes the place of, as the file reads now.
    pub fn replaces(&self) -> Option<(usize, usize)> {
        let ins = self.insert.as_ref().filter(|i| !i.replaced.is_empty())?;
        let first = self.content.get(..ins.offset)?.matches('\n').count() + 1;
        Some((first, first + ins.replaced.matches('\n').count()))
    }

    /// What it adds to the file that is there, as the question shows it.
    pub fn shown(&self) -> String {
        let added = self.insert.as_ref().map(|i| i.added.as_str()).unwrap_or("");
        added.trim_start_matches(',').trim_start_matches(['\r', '\n']).trim_end_matches(['\r', '\n']).replace("\r\n", "\n")
    }

    /// Whether it waits for the user's yes: it changes a file that was there, or a link takes it outside the repository
    /// at `root`, a new file there too.
    pub fn needs_yes(&self, root: &Path) -> bool {
        self.edits() || through_link(&self.path, root).map_or(false, |l| l.outside)
    }
}

/// A write that a link takes elsewhere: the file it lands in, whether the path itself is the link (else a folder on
/// its way is), and whether that file is outside the repository.
pub struct Link {
    pub real: PathBuf,
    pub is_link: bool,
    pub outside: bool,
}

/// Where a write to `p` lands, each link on its way followed, a dangling one too.
pub fn real_path(p: &Path) -> PathBuf {
    let mut cur = p.to_path_buf();
    for _ in 0..40 {
        let is_link = std::fs::symlink_metadata(&cur).map_or(false, |m| m.file_type().is_symlink());
        let Some(to) = is_link.then(|| std::fs::read_link(&cur).ok()).flatten() else { break };
        cur = if to.is_absolute() { to } else { cur.parent().map(|d| d.join(&to)).unwrap_or(to) };
    }
    crate::sources::resolve_path(&cur)
}

/// The repository a path rb writes for `root` belongs to: its git top, else `root`, resolved.
fn repo_top(root: &Path) -> PathBuf {
    crate::sources::git_top(root).map(|t| crate::sources::resolve_path(&t)).unwrap_or_else(|| crate::sources::resolve_path(root))
}

/// The link a write to `path` (a path in the checkout at `root`) goes through, when one does: a part of the path below
/// `root` that is a symbolic link (a name the file system matches in another case is no link).
pub fn through_link(path: &Path, root: &Path) -> Option<Link> {
    let rel = path.strip_prefix(root).ok()?;
    let mut at = root.to_path_buf();
    let linked = rel.components().any(|c| {
        at.push(c);
        std::fs::symlink_metadata(&at).map_or(false, |m| m.file_type().is_symlink())
    });
    if !linked {
        return None;
    }
    let real = real_path(path);
    let is_link = std::fs::symlink_metadata(path).map_or(false, |m| m.file_type().is_symlink());
    Some(Link { outside: !real.starts_with(repo_top(root)), real, is_link })
}

/// Where a link takes a write to `path`, as rb's text says it after the path: "" with no link,
/// " (a link to CLAUDE.md)", " (through a link, at ~/.claude/skills/rabbit-brain/SKILL.md, outside this repository)".
pub fn link_note(path: &Path, root: &Path) -> String {
    let Some(l) = through_link(path, root) else { return String::new() };
    let top = repo_top(root);
    let home = crate::sources::resolve_path(&home());
    let at = match (crate::sources::rel_to(&l.real, &top), crate::sources::rel_to(&l.real, &home)) {
        (Some(r), _) => r,
        (None, Some(r)) => format!("~/{r}"),
        _ => crate::sys::slashed(&l.real),
    };
    let out = if l.outside { ", outside this repository" } else { "" };
    if l.is_link {
        format!(" (a link to {at}{out})")
    } else {
        format!(" (through a link, at {at}{out})")
    }
}

/// A path rb writes, as its text names it: from `root`, with where a link takes the write.
pub fn named(path: &Path, root: &Path) -> String {
    format!("{}{}", crate::sources::rel_to(path, root).unwrap_or_else(|| crate::sys::slashed(path)), link_note(path, root))
}

/// Write an install, in a preview kept with what the preview would write, and keep it in the ownership ledger of the
/// checkout at `ledger` (the directory the command works in). A file that is there is written in place, so it keeps
/// its mode.
fn put(i: &Install, ledger: &Path) -> Res<PathBuf> {
    let (before, made) = (crate::owned::sha256_of(&i.path), crate::owned::missing_dirs(&i.path));
    let was = std::fs::metadata(&i.path).ok().map(|m| crate::sys::mtime_ns(&m));
    let r = if crate::dry::active() {
        crate::dry::write(&i.path, i.content.as_bytes())
    } else {
        (|| -> std::io::Result<()> {
            if let Some(p) = i.path.parent() {
                std::fs::create_dir_all(p)?;
            }
            std::fs::write(&i.path, &i.content)
        })()
    };
    r.map_err(|e| RBError::could_not_write(&e, &i.path))?;
    let mut more = crate::value::Dict::new();
    if let Some(ins) = &i.insert {
        // where rb's bytes went, so rb remove takes exactly them out while the file is as rb left it
        more.insert("offset".into(), Value::Int(ins.offset as i64));
        more.insert("length".into(), Value::Int(ins.added.len() as i64));
        more.insert("added_sha256".into(), Value::Str(crate::rbyaml::sha256_hex(ins.added.as_bytes())));
    }
    if let Some(old) = &i.old {
        // the file that was there, which rb remove writes back while rb's text is as rb wrote it
        more.insert("before".into(), Value::Str(old.clone()));
    }
    if i.edits() {
        // and the file's time then, which it gets back with its bytes
        if let Some(ns) = was {
            more.insert("mtime_before".into(), Value::Int(ns as i64));
        }
    }
    let before = if i.edits() { before } else { None };
    let made = if i.carry.contains_key("made") { Vec::new() } else { made };
    for (k, v) in &i.carry {
        more.insert(k.clone(), v.clone());
    }
    crate::owned::wrote(ledger, crate::owned::scope_of(ledger, &i.path), &i.path, i.how, before.as_deref(), &made, more);
    Ok(i.path.clone())
}

/// How rb stands to a file at a path of its own (the skill, the Cursor rule): not there, there as rb writes it, there as
/// rb last wrote it here (the ledger's line for it), or a file rb did not write, which rb changes only once agreed.
pub enum Own {
    Make(Install),
    Already(PathBuf),
    Theirs(Install),
}

/// What rb would do with its own file at `path` (`content`, as rb writes it), for the checkout at `ledger`.
pub fn own_file(path: PathBuf, content: String, ledger: &Path) -> Res<Own> {
    let Ok(bytes) = crate::dry::read(&path) else { return Ok(Own::Make(Install::new(path, "created", content))) };
    if bytes == content.as_bytes() {
        return Ok(Own::Already(path));
    }
    let now = crate::rbyaml::sha256_hex(&bytes);
    let lines = crate::owned::entries(ledger);
    let ours = crate::owned::last_for(&lines, &path, &["created", "replaced"]).filter(|l| l.get("sha256_after").and_then(Value::as_str) == Some(now.as_str()));
    if let Some(line) = ours {
        // rb's own file, as rb last wrote it here: written again, the line keeping what was there before rb
        let how = if line.get("how").and_then(Value::as_str) == Some("replaced") { "replaced" } else { "created" };
        let mut i = Install::new(path, how, content);
        for k in ["sha256_before", "before", "mtime_before", "made"] {
            if let Some(v) = line.get(k) {
                i.carry.insert(k.into(), v.clone());
            }
        }
        return Ok(Own::Make(i));
    }
    let shown = crate::sys::slashed(&path);
    let old = String::from_utf8(bytes).map_err(|e| {
        let said = crate::rbyaml::not_utf8(e.as_bytes(), &e.utf8_error());
        RBError::msg_fix("E_WRITE_FAILED", format!("{shown} is {said}, and rb did not write it, so rb leaves it as it is."), "Move it aside first if you want rb's in its place.")
    })?;
    let mut i = Install::new(path, "replaced", content);
    i.old = Some(old);
    Ok(Own::Theirs(i))
}

fn home() -> PathBuf {
    if cfg!(windows) {
        return crate::sys::home();
    }
    std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/"))
}

fn here_or(base: Option<&Path>) -> PathBuf {
    base.map(|b| b.to_path_buf()).unwrap_or_else(|| std::env::current_dir().unwrap_or_default())
}

/// The skill's file: in the project's .claude/skills/, or with `project` false the home directory's.
pub fn claude_path(project: bool, base: Option<&Path>) -> PathBuf {
    let root = if project { here_or(base).join(".claude") } else { home().join(".claude") };
    root.join("skills").join("rabbit-brain").join("SKILL.md")
}

pub fn cursor_path(base: Option<&Path>) -> PathBuf {
    here_or(base).join(".cursor").join("rules").join("rabbit-brain.mdc")
}

/// The rule Cursor reads: the skill's body under Cursor's own frontmatter.
pub fn cursor_text(text: &str) -> String {
    let (description, body) = split(text);
    format!("---\ndescription: {description}\nalwaysApply: false\n---\n\n{body}")
}

/// The home directory's skill, which rb skill add writes again whatever it holds: rb's for every project, outside any
/// repository, and never taken out by rb remove.
fn home_claude(text: &str) -> Res<PathBuf> {
    put(&Install::new(claude_path(false, None), "created", text.to_string()), &here_or(None))
}

/// rb's marked section, as rb writes it into AGENTS.md (with "\n"; a file with "\r\n" gets it in its own).
pub fn agents_block(text: &str) -> String {
    let (_, body) = split(text);
    format!("{BEGIN}\n\n{}\n\n{END}\n", body.trim_end())
}

/// What rb would write to AGENTS.md: the file made with rb's section, or the section added to the file that is there
/// (in place of the section between rb's marker lines where it holds one, else at its end), every other byte kept.
/// None when the section is there already as rb writes it. A file rb cannot read whole it does not write over. The
/// ledger line keeps rb's section and the bytes it took the place of; where the file is as rb's own earlier write here
/// left it, that write is undone first, so the line still says what was there before rb.
fn agents_install(text: &str, base: Option<&Path>) -> Res<Option<Install>> {
    let block = agents_block(text);
    let root = here_or(base);
    let path = root.join("AGENTS.md");
    let keep = |i: &mut Install, section: String, replaced: &str| {
        i.carry.insert("section".into(), Value::Str(section));
        i.carry.insert("replaced".into(), Value::str(replaced));
    };
    if !crate::dry::exists(&path) {
        let mut i = Install::new(path, "created", block.clone());
        keep(&mut i, crate::splice::section_body("", &block), "");
        return Ok(Some(i));
    }
    let shown = crate::sys::slashed(&path);
    let bytes = crate::dry::read(&path).map_err(|e| {
        RBError::msg_fix("E_WRITE_FAILED", format!("{shown} could not be read, so rb's section was not added to it: {}", crate::sys::os_words(&e)), "Check that it is a file you can read and write.")
    })?;
    let existing = String::from_utf8(bytes).map_err(|e| {
        let said = crate::rbyaml::not_utf8(e.as_bytes(), &e.utf8_error());
        RBError::msg_fix("E_WRITE_FAILED", format!("{shown} is {said}, so rb's section was not added to it."), "Save it as UTF-8, or put rb in place another way: rb skill add --claude, or --cursor.")
    })?;
    let lines = crate::owned::entries(&root);
    let prev = crate::owned::last_for(&lines, &path, &["section", "created"]);
    let str_of = |l: &crate::value::Dict, k: &str| l.get(k).and_then(Value::as_str).map(str::to_string);
    if let Some(prev) = prev.as_ref().filter(|l| str_of(l, "sha256_after").as_deref() == Some(crate::rbyaml::sha256_hex(existing.as_bytes()).as_str())) {
        if str_of(prev, "how").as_deref() == Some("created") {
            if existing == block {
                return Ok(None);
            }
            let mut i = Install::new(path, "created", block.clone());
            keep(&mut i, crate::splice::section_body("", &block), "");
            return Ok(Some(i));
        }
        if let Some(original) = crate::owned::as_before(&existing, prev) {
            let ins = crate::splice::section_add(&original, &block, BEGIN, END);
            if ins.text == existing {
                return Ok(None);
            }
            let mut i = Install::new(path, "section", ins.text.clone());
            keep(&mut i, crate::splice::section_body(&original, &block), &ins.replaced);
            for k in ["sha256_before", "mtime_before"] {
                if let Some(v) = prev.get(k) {
                    i.carry.insert(k.into(), v.clone());
                }
            }
            i.insert = Some(ins);
            return Ok(Some(i));
        }
    }
    let ins = crate::splice::section_add(&existing, &block, BEGIN, END);
    if ins.text == existing {
        return Ok(None);
    }
    let mut i = Install::new(path, "section", ins.text.clone());
    // rb's own section from an earlier write here, replaced in place: what it took the place of is what was there
    let replaced = prev.as_ref().filter(|l| !ins.replaced.is_empty() && str_of(l, "section").as_deref() == Some(ins.replaced.as_str())).map_or(ins.replaced.clone(), |l| str_of(l, "replaced").unwrap_or_default());
    keep(&mut i, crate::splice::section_body(&existing, &block), &replaced);
    i.insert = Some(ins);
    Ok(Some(i))
}

pub fn mcp_server() -> Value {
    crate::obj! {"command" => "rb", "args" => crate::list!["mcp"]}
}

/// What rb would write to the project's .mcp.json: a new file holding rb's server, or rb's server added to the file
/// that is there, never changing an entry it did not write or reformatting the file. Ok(None): it is there already.
/// Err: why the file is left as it is.
fn mcp_install(root: &Path) -> Result<Option<Install>, String> {
    let path = root.join(".mcp.json");
    if !crate::dry::exists(&path) {
        let cfg = crate::obj! {"mcpServers" => crate::obj! {"rabbit-brain" => mcp_server()}};
        return Ok(Some(Install::new(path, "created", pyjson::dumps_with(&cfg, &Dumps::indent(2)).unwrap_or_default() + "\n")));
    }
    let Some(text) = crate::dry::read(&path).ok().and_then(|b| String::from_utf8(b).ok()) else { return Err("is not JSON rb can read".into()) };
    match crate::splice::mcp_add(&text, "rabbit-brain", &mcp_server()) {
        Ok(ins) => {
            let mut i = Install::new(path, "mcp_entry", ins.text.clone());
            i.insert = Some(ins);
            Ok(Some(i))
        }
        Err(why) if why == "already" => Ok(None),
        Err(why) => Err(why),
    }
}

/// The coding agents that work in this repository, each with the files that gave it away. An agent's session gives
/// its agent away too, and is never among the signs: rb's words never say who ran rb.
pub fn detect_agents(root: &Path) -> indexmap::IndexMap<String, Vec<String>> {
    let runtime = crate::actor::detect_runtime().map(|r| r.0).unwrap_or_default();
    let mut found: indexmap::IndexMap<String, Vec<String>> = indexmap::IndexMap::new();
    let checks: [(&str, Option<&str>, bool); 9] = [
        ("claude", Some(".claude/"), root.join(".claude").is_dir()),
        ("claude", Some("CLAUDE.md"), root.join("CLAUDE.md").is_file()),
        ("claude", None, runtime == "claude-code"),
        ("codex", Some(".codex/"), root.join(".codex").is_dir()),
        ("codex", None, runtime == "codex"),
        ("agents_md", Some("AGENTS.md"), root.join("AGENTS.md").is_file()),
        ("cursor", Some(".cursor/"), root.join(".cursor").is_dir()),
        ("cursor", Some(".cursorrules"), root.join(".cursorrules").is_file()),
        ("cursor", None, runtime == "cursor"),
    ];
    for (agent, sign, present) in checks {
        if present {
            let signs = found.entry(agent.to_string()).or_default();
            signs.extend(sign.map(str::to_string));
        }
    }
    found
}

/// An agent rb set up, with the files that gave it away when any did.
fn with_signs(who: &str, signs: &[String]) -> String {
    if signs.is_empty() {
        who.to_string()
    } else {
        format!("{who} ({})", signs.join(", "))
    }
}

/// The command that puts rb's own file at `path` in place of one rb did not write (the skill, the Cursor rule): with
/// the yes given up front, but in an agent session, where only the user agrees, in their own terminal.
fn own_command(path: &Path) -> String {
    let base = if path.file_name().map_or(false, |n| n == "rabbit-brain.mdc") { "rb skill add --cursor" } else { "rb skill add --claude --project" };
    format!("{base}{}", yes_flag())
}

/// What a command rb names for a file that was there takes to agree: `--yes` for a script, nothing in an agent session
/// (where the user runs it in their own terminal, and it asks).
fn yes_flag() -> &'static str {
    if session_runtime().is_some() {
        ""
    } else {
        " --yes"
    }
}

/// How rb says it left a file that was there as it is.
fn left_words(rel: &str) -> String {
    if session_runtime().is_some() {
        format!("{rel} was here before rb, and in an agent session rb changes no file that was there, so rb left it as it is. In the user's own terminal,")
    } else {
        format!("{rel} was here before rb, so rb left it as it is.")
    }
}

/// The command that writes rb's file at `path` for one agent, as `rb skill add` does: with the yes up front, but in an
/// agent session.
fn add_command(path: &Path) -> String {
    match path.file_name().and_then(|n| n.to_str()) {
        Some("AGENTS.md") => format!("rb skill add --agents-md{}", yes_flag()),
        Some(".mcp.json") => "claude mcp add --scope project rabbit-brain -- rb mcp".to_string(),
        _ => own_command(path),
    }
}

/// What rb says of a file that is there and that it left as it is, with what to add to it and the one command that adds
/// it, as --json gives it too (`left`); for a new file a link takes outside the repository, that rb wrote nothing there.
fn left_as_it_is(i: &Install, root: &Path) -> (String, Value) {
    let rel = crate::sources::rel_to(&i.path, root).unwrap_or_else(|| crate::sys::slashed(&i.path));
    let name = named(&i.path, root);
    if i.how == "created" {
        let command = add_command(&i.path);
        let said = if session_runtime().is_some() {
            format!("rb wrote nothing to {name}, since rb writes outside this repository only once the user agrees. In the user's own terminal, {command} writes it.")
        } else {
            format!("rb wrote nothing to {name}, since rb writes outside this repository only once agreed. {command} writes it.")
        };
        return (said, crate::obj! {"path" => rel, "adds" => i.content.clone(), "command" => command});
    }
    let (left, yes) = (left_words(&name), yes_flag());
    let (said, command, adds) = match i.how {
        "section" => (
            match i.replaces() {
                Some((a, b)) => format!("{left} rb skill add --agents-md{yes} puts rb's section (the text rb skill prints, between two marker lines) in place of the one between rb's markers, lines {a} to {b}."),
                None => format!("{left} rb skill add --agents-md{yes} adds rb's section at its end (the text rb skill prints, between two marker lines)."),
            },
            format!("rb skill add --agents-md{yes}"),
            i.shown(),
        ),
        "replaced" => {
            let command = own_command(&i.path);
            (
                format!("{} {command} puts rb's in its place, and rb remove puts this one back.", left.replacen(" was here before rb", " was here before rb and is not rb's", 1)),
                command,
                i.content.clone(),
            )
        }
        _ => {
            let member = format!("\"rabbit-brain\": {}", pyjson::dumps(&mcp_server()));
            (
                format!("{name} was here before rb, so rb left it as it is. To add rb's MCP server, put {member} in its mcpServers, or run: claude mcp add --scope project rabbit-brain -- rb mcp"),
                "claude mcp add --scope project rabbit-brain -- rb mcp".to_string(),
                i.shown(),
            )
        }
    };
    (said, crate::obj! {"path" => rel, "adds" => adds, "command" => command})
}

/// The one question rb asks before it changes files that were there before it, with exactly what each change adds
/// (or, for a file rb's own takes the place of, which text it puts there).
pub fn question(edits: &[&Install], root: &Path) -> Vec<String> {
    let names: Vec<String> = edits.iter().map(|i| named(&i.path, root)).collect();
    let there: Vec<&String> = edits.iter().zip(&names).filter(|(i, _)| i.edits()).map(|(_, n)| n).collect();
    let one = there.len() == 1;
    let (it, are) = (if one { "it" } else { "them" }, if one { "was" } else { "were" });
    let joined = there.iter().map(|n| n.as_str()).collect::<Vec<_>>().join(" and ");
    let mut lines = Vec::new();
    if !there.is_empty() {
        lines.push(if edits.iter().any(|i| i.old.is_some() || i.replaces().is_some()) {
            format!("{joined} {are} here before rb. rb would change {it} as below, and nothing else:")
        } else {
            format!("{joined} {are} here before rb. rb would add this to {it}, and change nothing else in {it}:")
        });
    }
    if edits.iter().any(|i| !i.edits()) {
        lines.push(format!("rb would {}write outside this repository, where a link takes it:", if there.is_empty() { "" } else { "also " }));
    }
    let all_one = edits.len() == 1;
    let it = if all_one { "it" } else { "them" };
    for (i, name) in edits.iter().zip(&names) {
        lines.push(String::new());
        match i.how {
            "created" => {
                lines.push(format!("{name}: a new file, with {}.", holds(&i.path)));
                continue;
            }
            "section" => lines.push(match i.replaces() {
                Some((a, b)) => format!("{name}, in place of the section between rb's markers now in it (lines {a} to {b}):"),
                None => format!("{name}, at its end:"),
            }),
            "replaced" => {
                lines.push(format!("{name}: in place of what it holds, {}; rb keeps what it holds now, for rb remove to put back.", holds(&i.path)));
                continue;
            }
            _ => lines.push(format!("{name}, in mcpServers:")),
        }
        lines.extend(i.shown().lines().map(str::to_string));
    }
    lines.push(String::new());
    lines.push(format!("rb remove takes {it} out again."));
    lines
}

/// What a file rb writes for an agent holds, as the question says it.
fn holds(path: &Path) -> &'static str {
    match path.file_name().and_then(|n| n.to_str()) {
        Some(".mcp.json") => "rb's MCP server (\"rabbit-brain\" in mcpServers, which runs `rb mcp`)",
        Some("AGENTS.md") => "rb's section, between its markers",
        Some(n) if n.ends_with(".mdc") => "rb's rule for Cursor (what `rb skill` prints, under Cursor's own heading)",
        _ => "rb's own text (what `rb skill` prints)",
    }
}

/// The question's last line, which names each file: "Add rb's server to .mcp.json and rb's section to AGENTS.md? [y/N] ".
pub fn prompt(edits: &[&Install], root: &Path) -> String {
    let name = |i: &Install| named(&i.path, root);
    let adds: Vec<String> = edits.iter().filter(|i| i.old.is_none() && i.edits()).map(|i| format!("rb's {} to {}", if i.how == "mcp_entry" { "server" } else { "section" }, name(i))).collect();
    let puts: Vec<String> = edits.iter().filter(|i| i.old.is_some()).map(|i| format!("rb's text in place of {}", name(i))).collect();
    let makes: Vec<String> = edits.iter().filter(|i| !i.edits()).map(|i| name(i)).collect();
    let mut parts = Vec::new();
    if !adds.is_empty() {
        parts.push(format!("add {}", adds.join(" and ")));
    }
    if !puts.is_empty() {
        parts.push(format!("put {}", puts.join(" and ")));
    }
    if !makes.is_empty() {
        parts.push(format!("write {}", makes.join(" and ")));
    }
    let said = parts.join(", and ");
    let mut chars = said.chars();
    let said = chars.next().map(|c| c.to_uppercase().collect::<String>() + chars.as_str()).unwrap_or_default();
    format!("{said}? [y/N] ")
}

/// Ask once, in the user's own terminal: what goes before is said first, then the question. Yes only for y or yes;
/// Enter, anything else and no answer keep the files as they are.
pub fn ask_once(said_before: Vec<String>, question: &[String], prompt: &str) -> bool {
    if !said_before.is_empty() {
        crate::cli::println_out(&said_before.join("\n"));
    }
    crate::cli::println_out(&question.join("\n"));
    crate::cli::stdout_write(prompt);
    let mut answer = String::new();
    if std::io::stdin().read_line(&mut answer).is_err() {
        return false;
    }
    matches!(answer.trim().to_lowercase().as_str(), "y" | "yes")
}

/// What setting up the agents said and wrote: its lines, the files it wrote, and the files that are there and that it
/// left as they are, with what to add to each.
#[derive(Default)]
pub struct SetUp {
    pub said: Vec<String>,
    pub written: Vec<String>,
    pub left: Vec<Value>,
    /// rb's own files that were there already as rb writes them: in place, though not written
    pub present: Vec<String>,
}

/// Install rb's instructions for the agents found here: the files that are not there are made; a file that is there
/// is changed only as `consent` allows, `ask` putting the one question where it asks. An instruction file rb cannot
/// write is said and left as it is: the record is started either way, and rb skill add installs them later.
pub fn setup_agents(root: &Path, consent: Consent, ask: &mut dyn FnMut(&[&Install]) -> bool) -> Res<SetUp> {
    let text = skill_text();
    let found = detect_agents(root);
    let mut done = SetUp::default();
    let left = |who: &str, signs: &[String], e: &RBError| format!("{}: nothing written. {}", with_signs(who, signs), e.message);
    // a skill or a rule at rb's path that rb did not write is left as it is: init makes only what is not there
    let claude = found.get("claude").map(|_| (own_file(claude_path(true, Some(root)), text.clone(), root), mcp_install(root)));
    let agents = (found.contains_key("agents_md") || found.contains_key("codex")).then(|| agents_install(&text, Some(root)));
    let rule = found.get("cursor").map(|_| own_file(cursor_path(Some(root)), cursor_text(&text), root));
    // the one question, for every file that is there and would change, and every new one a link takes outside
    let mut edits: Vec<&Install> = Vec::new();
    if let Some((skill, mcp)) = &claude {
        if let Ok(Own::Make(i)) = skill {
            if i.needs_yes(root) {
                edits.push(i);
            }
        }
        if let Ok(Some(i)) = mcp {
            if i.needs_yes(root) {
                edits.push(i);
            }
        }
    }
    if let Some(Ok(Some(i))) = &agents {
        if i.needs_yes(root) {
            edits.push(i);
        }
    }
    if let Some(Ok(Own::Make(i))) = &rule {
        if i.needs_yes(root) {
            edits.push(i);
        }
    }
    let agreed = !edits.is_empty()
        && match consent {
            Consent::Yes => true,
            Consent::No => false,
            Consent::Ask => crate::dry::active() || ask(&edits),
        };
    // rb's own file: made, as it was, or one rb did not write, left as it is with the command that puts rb's there
    let own = |done: &mut SetUp, file: &Res<Own>, who: &str, signs: &[String], what: &str| -> Option<String> {
        match file {
            Ok(Own::Make(i)) if i.needs_yes(root) && !agreed => {
                let (said, value) = left_as_it_is(i, root);
                done.left.push(value);
                Some(said)
            }
            Ok(Own::Make(i)) => match put(i, root) {
                Ok(path) => {
                    done.written.push(crate::sys::slashed(path));
                    Some(format!("{what}{}", named(&i.path, root)))
                }
                Err(e) => {
                    done.said.push(left(who, signs, &e));
                    None
                }
            },
            Ok(Own::Already(path)) => {
                done.present.push(crate::sys::slashed(path));
                Some(format!("{what}{}, as it was", named(path, root)))
            }
            Ok(Own::Theirs(i)) => {
                let (said, value) = left_as_it_is(i, root);
                done.left.push(value);
                Some(said)
            }
            Err(e) => {
                done.said.push(left(who, signs, e));
                None
            }
        }
    };
    if let (Some(signs), Some((skill, mcp))) = (found.get("claude"), &claude) {
        let who = with_signs("Claude Code", signs);
        if let Some(skill_said) = own(&mut done, skill, "Claude Code", signs, "the skill in ") {
            let mcp_name = named(&root.join(".mcp.json"), root);
            let mcp_said = match mcp {
                Ok(Some(i)) if !i.needs_yes(root) || agreed => match put(i, root) {
                    Ok(path) => {
                        done.written.push(crate::sys::slashed(path));
                        if i.edits() { format!("rb's MCP server added to {mcp_name}") } else { format!("rb's MCP server in a new {mcp_name}") }
                    }
                    Err(e) => format!("{mcp_name}: nothing written. {}", e.message),
                },
                Ok(Some(i)) => {
                    let (said, value) = left_as_it_is(i, root);
                    done.left.push(value);
                    said
                }
                Ok(None) => format!("rb's MCP server was already in {mcp_name}"),
                Err(why) => format!("{mcp_name} {why}, so it is left as it is"),
            };
            done.said.push(format!("{who}: {}; {mcp_said}", skill_said.trim_end_matches('.')));
        }
    }
    if let Some(planned) = agents {
        let mut signs = found.get("agents_md").cloned().unwrap_or_default();
        signs.extend(found.get("codex").cloned().unwrap_or_default());
        let who = if found.contains_key("codex") { "Codex and the agents that read AGENTS.md" } else { "The agents that read AGENTS.md" };
        let name = named(&root.join("AGENTS.md"), root);
        match planned {
            Ok(Some(i)) if !i.needs_yes(root) || agreed => match put(&i, root) {
                Ok(path) => {
                    done.written.push(crate::sys::slashed(path));
                    done.said.push(format!("{}: rb's section in {name}, between its markers", with_signs(who, &signs)));
                }
                Err(e) => done.said.push(left(who, &signs, &e)),
            },
            Ok(Some(i)) => {
                let (said, value) = left_as_it_is(&i, root);
                done.left.push(value);
                done.said.push(format!("{}: {said}", with_signs(who, &signs)));
            }
            Ok(None) => done.said.push(format!("{}: rb's section in {name}, between its markers, as it was", with_signs(who, &signs))),
            Err(e) => done.said.push(left(who, &signs, &e)),
        }
    }
    if let (Some(signs), Some(rule)) = (found.get("cursor"), &rule) {
        if let Some(said) = own(&mut done, rule, "Cursor", signs, "") {
            done.said.push(format!("{}: {said}", with_signs("Cursor", signs)));
        }
    }
    if found.is_empty() {
        done.said.push("No agent's instructions here yet. rb skill add puts rb's for one: --claude (Claude Code), --agents-md (Codex and others), --cursor".into());
    }
    Ok(done)
}

/// The sign of the agent session that runs this command, read as widely as rb confirm reads one (a chat's leftover
/// id too), when it is a chat session: a CI job is none.
pub fn session_runtime() -> Option<String> {
    match crate::actor::detect_runtime() {
        Some((runtime, _)) if runtime != "github-actions" => Some(runtime),
        _ => crate::actor::SESSION_LEFTOVERS.iter().find(|k| std::env::var(k).map_or(false, |v| !v.trim().is_empty())).map(|k| k.to_string()),
    }
}

/// The commands that put rb's instructions in place for the agents found here, for their later sessions: a running
/// session loads neither a skill nor an MCP server added during it.
pub fn later_commands(root: &Path) -> Vec<String> {
    let found = detect_agents(root);
    let mut later = Vec::new();
    if found.contains_key("claude") {
        later.push("Claude Code: rb skill add --claude --project, and claude mcp add --scope project rabbit-brain -- rb mcp".to_string());
    }
    if found.contains_key("agents_md") || found.contains_key("codex") {
        later.push(format!("{}: rb skill add --agents-md", if found.contains_key("codex") { "Codex and the agents that read AGENTS.md" } else { "The agents that read AGENTS.md" }));
    }
    if found.contains_key("cursor") {
        later.push("Cursor: rb skill add --cursor".to_string());
    }
    if later.is_empty() {
        later.push("rb skill add puts rb's for one: --claude (Claude Code), --agents-md (Codex and others), --cursor".to_string());
    }
    later
}

/// The files written here that the repository's .gitignore keeps out of git, relative to `root`.
pub fn ignored(root: &Path, written: &[String]) -> Vec<String> {
    written
        .iter()
        .filter_map(|w| {
            let rel = Path::new(w).strip_prefix(root).ok().map(|p| p.to_string_lossy().replace('\\', "/")).unwrap_or_else(|| w.clone());
            crate::sources::git(root, &["check-ignore", "-q", "--", &rel]).map(|_| rel)
        })
        .collect()
}

pub fn cmd_skill(ns: &Namespace, out: &mut Out) -> Res<i32> {
    let text = skill_text();
    if crate::cmds::s(ns, "action").as_deref() != Some("add") {
        out.say(text.trim_end_matches('\n').to_string());
        out.data = crate::dict! {"markdown" => text};
        return Ok(0);
    }
    let (mut c, cur, am) = (crate::cmds::flag(ns, "claude"), crate::cmds::flag(ns, "cursor"), crate::cmds::flag(ns, "agents_md"));
    if !c && !cur && !am {
        c = true;
    }
    let here = std::env::current_dir().unwrap_or_default();
    let mut written: Vec<String> = Vec::new();
    if c && !crate::cmds::flag(ns, "project") {
        written.push(crate::sys::slashed(home_claude(&text)?));
    }
    let mut lines: Vec<String> = written.iter().map(|p| format!("wrote {p}")).collect();
    // a file that is there changes only as init's would: asked once in the user's own terminal, or with --yes
    let mut plain: Vec<Install> = Vec::new();
    let mut edits: Vec<Install> = Vec::new();
    // rb's own files there already as rb writes them: in place, and said when .gitignore keeps them out of git
    let mut present: Vec<String> = Vec::new();
    let mut want = |i: Install| if i.needs_yes(&here) { edits.push(i) } else { plain.push(i) };
    if c && crate::cmds::flag(ns, "project") {
        match own_file(claude_path(true, None), text.clone(), &here)? {
            Own::Make(i) | Own::Theirs(i) => want(i),
            Own::Already(path) => {
                lines.push(".claude/skills/rabbit-brain/SKILL.md holds rb's skill already, as rb writes it.".into());
                present.push(crate::sys::slashed(path));
            }
        }
    }
    if cur {
        match own_file(cursor_path(None), cursor_text(&text), &here)? {
            Own::Make(i) | Own::Theirs(i) => want(i),
            Own::Already(path) => {
                lines.push(".cursor/rules/rabbit-brain.mdc holds rb's rule already, as rb writes it.".into());
                present.push(crate::sys::slashed(path));
            }
        }
    }
    if am {
        match agents_install(&text, None)? {
            None => lines.push("AGENTS.md holds rb's section already, as rb writes it.".into()),
            Some(i) => want(i),
        }
    }
    let mut left: Vec<Value> = Vec::new();
    for i in &plain {
        let path = crate::sys::slashed(put(i, &here)?);
        lines.push(format!("wrote {path}{}", link_note(&i.path, &here)));
        written.push(path);
    }
    if !edits.is_empty() {
        let shown: Vec<&Install> = edits.iter().collect();
        let agreed = match consent(crate::cmds::flag(ns, "yes"), out.json_mode) {
            Consent::Yes => true,
            Consent::No => false,
            Consent::Ask => ask_once(std::mem::take(&mut lines), &question(&shown, &here), &prompt(&shown, &here)),
        };
        for i in &edits {
            if agreed {
                let path = crate::sys::slashed(put(i, &here)?);
                lines.push(format!("wrote {path}{}", link_note(&i.path, &here)));
                written.push(path);
            } else {
                let (said, value) = left_as_it_is(i, &here);
                lines.push(said);
                left.push(value);
            }
        }
    }
    let kept_out = ignored(&here, &[written.clone(), present].concat());
    lines.extend(kept_out.iter().map(|p| ignored_said(p)));
    lines.push("The agent picks it up in its next session. The skill defers to `rb docs` for the full rules.".into());
    out.say_all(lines);
    out.data = crate::dict! {"written" => Value::list_of_str(written), "ignored" => Value::list_of_str(kept_out)};
    if am || cur || (c && crate::cmds::flag(ns, "project")) {
        out.data.insert("left".into(), Value::List(left));
    }
    Ok(0)
}

/// What rb says of a file it installed that .gitignore keeps out of git.
pub fn ignored_said(path: &str) -> String {
    format!(".gitignore keeps {path} out of git, so it does not travel with the repository: a clone, and the agents working in it, will not have it.")
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The question says what each new file holds: rb's server for .mcp.json, its section for AGENTS.md, its rule for
    /// Cursor, and the skill's text only for the skill.
    #[test]
    fn the_question_says_what_each_new_file_holds() {
        let root = Path::new("/p");
        let made = |rel: &str| Install::new(root.join(rel), "created", String::new());
        let (mcp, agents, rule, skill) = (made(".mcp.json"), made("AGENTS.md"), made(".cursor/rules/rabbit-brain.mdc"), made(".claude/skills/rabbit-brain/SKILL.md"));
        let said = question(&[&mcp, &agents, &rule, &skill], root).join("\n");
        assert!(said.contains("\n.mcp.json: a new file, with rb's MCP server (\"rabbit-brain\" in mcpServers, which runs `rb mcp`).\n"), "{said}");
        assert!(said.contains("\nAGENTS.md: a new file, with rb's section, between its markers.\n"), "{said}");
        assert!(said.contains("\n.cursor/rules/rabbit-brain.mdc: a new file, with rb's rule for Cursor (what `rb skill` prints, under Cursor's own heading).\n"), "{said}");
        assert!(said.contains("\n.claude/skills/rabbit-brain/SKILL.md: a new file, with rb's own text (what `rb skill` prints).\n"), "{said}");
    }
}
