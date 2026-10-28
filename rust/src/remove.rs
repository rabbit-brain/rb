//! rb remove: take rb out of this checkout, and only what checks out as rb's. The plan comes first, in three groups:
//! what rb will remove and how it knows each is its own (a log hash, a ledger hash, rb's own marks or rb's own text),
//! what it will keep and why, and what is not rb's to touch. It deletes no git object, worktree, history or research
//! file; the objects only rb's refs reached are named and left for git's own gc.

use crate::cli::Out;
use crate::errors::{RBError, Res};
use crate::ledger::{Ledger, DIR, GITATTRIBUTES, GITIGNORE};
use crate::value::{Dict, Value};
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

/// What one removal does on disk.
#[derive(Debug, Clone)]
enum Act {
    File(PathBuf),
    /// a file rb added to, written back without rb's part, with the time it had before rb's write where it is as it was
    Write(PathBuf, String, Option<i64>),
    Ref(String),
    /// a folder rb made, taken away when nothing is left in it
    Empty(PathBuf),
    /// rb's list of what it wrote (the ledger, or rb's folder in git's that holds it): taken away last, and only when
    /// nothing before it failed, so that rb remove run again can finish what this one could not
    Ledger(PathBuf),
}

#[derive(Debug, Clone)]
struct Item {
    path: String,
    why: String,
    acts: Vec<Act>,
}

#[derive(Debug, Clone)]
struct Kept {
    path: String,
    why: String,
}

/// What rb remove would do in a checkout: what it takes out, what it keeps, and what it only names.
pub struct Plan {
    root: PathBuf,
    remove: Vec<Item>,
    keep: Vec<Kept>,
    residue: Vec<String>,
    listed: Vec<Kept>,
    others: Vec<Kept>,
    uncommitted: usize,
    tracked: usize,
    /// tracked files rb writes back (rb's part taken out) whose bytes then differ from what git holds for them
    changed: usize,
    /// the files rb deletes or writes back that git's index holds staged with rb's writes, which rb leaves as they are
    staged: Vec<String>,
    /// of those, the files rb writes back that are staged in part, rb's part with other staged changes, which no one
    /// command takes apart
    staged_in_part: Vec<String>,
    /// the command that takes them out of the index, run where rb remove was
    unstage: String,
    copy_first: String,
    /// how the plan names this checkout's .rb/
    record_path: String,
    /// whether the checkout is in git: a record outside git is in no commit at all
    in_git: bool,
    /// the files of the plan a link takes elsewhere: (how the plan names it, where the link takes it as said, the file)
    links: Vec<(String, String, String)>,
    /// the skill every project reads (the home directory's), when this checkout's own rb init wrote it there
    home_skill_named: bool,
}

/// Every .rb/.gitignore rb has written, as rb wrote it: one with a line added since is the user's.
const GITIGNORES: [&str; 3] = [GITIGNORE, ".lock\n*.tmp\ncache/\njournal/\nlegacy-json/\n", ".lock\n*.tmp\nobjects/\n"];

/// What is not rb's to touch, whatever the plan holds.
const UNTOUCHED: &str = "Not rb's to touch: your research files, worktrees and .git/worktrees, git history, git objects, config, hooks and the index.";

fn sha(data: &[u8]) -> String {
    crate::rbyaml::sha256_hex(data)
}

fn line_str<'a>(l: &'a Dict, k: &str) -> Option<&'a str> {
    l.get(k).and_then(Value::as_str)
}

/// The folders a ledger line says its write made, innermost first: taken away when nothing else is in them.
fn made_dirs(line: Option<&Dict>) -> Vec<Act> {
    let mut dirs: Vec<PathBuf> = line
        .and_then(|l| l.get("made"))
        .and_then(Value::as_list)
        .map(|l| l.iter().filter_map(Value::as_str).map(PathBuf::from).collect())
        .unwrap_or_default();
    dirs.reverse();
    dirs.into_iter().map(Act::Empty).collect()
}

/// Every file under `dir`, and every folder, deepest first.
/// Taking away a file rb made at `path`: where `path` is itself a link (the user's: rb makes none), rb's write landed in
/// the file it points to, which goes while the link stays.
fn made_at(path: &Path) -> Act {
    Act::File(if is_link(path) { crate::skill::real_path(path) } else { path.to_path_buf() })
}

fn is_link(path: &Path) -> bool {
    std::fs::symlink_metadata(path).map_or(false, |m| m.file_type().is_symlink())
}

/// What a plan says beside a file rb made at a link: the link stays.
fn link_stays(path: &Path) -> &'static str {
    if is_link(path) {
        "; the link stays"
    } else {
        ""
    }
}

fn walk(dir: &Path) -> (Vec<PathBuf>, Vec<PathBuf>) {
    let (mut files, mut dirs) = (Vec::new(), Vec::new());
    let mut todo = vec![dir.to_path_buf()];
    while let Some(d) = todo.pop() {
        dirs.push(d.clone());
        if let Ok(rd) = std::fs::read_dir(&d) {
            for e in rd.flatten() {
                let p = e.path();
                if e.file_type().map_or(false, |t| t.is_dir()) {
                    todo.push(p);
                } else {
                    files.push(p);
                }
            }
        }
    }
    files.sort();
    dirs.sort_by_key(|d| std::cmp::Reverse(d.components().count()));
    (files, dirs)
}

impl Plan {
    /// The plan for the checkout `cwd` is in: the record a command there writes to, else the repository's top.
    pub fn of(cwd: &Path) -> Res<Plan> {
        let root = crate::ledger::find_root(Some(cwd)).unwrap_or_else(|| crate::sources::git_top(cwd).unwrap_or_else(|| crate::sources::resolve_path(cwd)));
        let top = crate::sources::git_top(&root);
        let in_git = top.is_some();
        let top = top.unwrap_or_else(|| root.clone());
        let mut plan = Plan { root: root.clone(), remove: Vec::new(), keep: Vec::new(), residue: Vec::new(), listed: Vec::new(), others: Vec::new(), uncommitted: 0, tracked: 0, changed: 0, staged: Vec::new(), staged_in_part: Vec::new(), unstage: String::new(), copy_first: String::new(), record_path: String::new(), in_git, links: Vec::new(), home_skill_named: false };
        let lines = crate::owned::entries(&root);
        let shown = |p: &Path| crate::sources::rel_to(p, &top).filter(|r| !r.is_empty()).unwrap_or_else(|| crate::sys::slashed(p));
        plan.record(&root, &top, in_git, &shown);
        plan.integrations(&root, &lines, &shown);
        plan.outputs(&root, &lines, &shown);
        if in_git {
            plan.clone_wide(&root, &top, &shown);
            plan.count_tracked(&top, cwd, &lines);
        }
        plan.listed();
        Ok(plan)
    }

    /// This checkout's .rb/: every file rb wrote (a record as its log says rb last wrote it, the log, rb's own text and
    /// copies) goes, and a file rb did not write, or one changed since, is kept and named. A .rb/ that is a link (the
    /// user's: rb makes none) is named with where it goes, and stays, with the folder it points to.
    fn record(&mut self, root: &Path, top: &Path, in_git: bool, shown: &dyn Fn(&Path) -> String) {
        let dir = root.join(DIR);
        if !dir.is_dir() {
            return;
        }
        let linked = is_link(&dir);
        let led = Ledger::new(root, "cli", None);
        let tampered: Result<HashMap<String, String>, String> = led
            .tampered()
            .map(|t| t.iter().map(|d| (d.get("path").and_then(Value::as_str).unwrap_or("").to_string(), d.get("why").and_then(Value::as_str).unwrap_or("").to_string())).collect())
            .map_err(|e| e.message);
        let records: HashSet<PathBuf> = led.record_files().into_iter().map(|(_, p)| p).collect();
        let (files, dirs) = walk(&dir);
        let (mut ours, mut kept) = (Vec::new(), 0usize);
        // rb's local files, which .rb/.gitignore keeps out of git: no commit is to keep them
        let local = |rel: &str| rel.starts_with("cache/") || rel.starts_with("journal/") || rel == ".lock" || rel.ends_with(".tmp");
        for f in &files {
            let rel = crate::sources::rel_to(f, &dir).unwrap_or_default();
            let text = || std::fs::read(f).unwrap_or_default();
            let verdict: Result<(), String> = if local(&rel) || rel == "log.jsonl" || rel == "merges.json" {
                Ok(())
            } else if rel == ".gitignore" {
                let t = text();
                if GITIGNORES.iter().any(|g| t == g.as_bytes()) { Ok(()) } else { Err("changed since rb wrote it".into()) }
            } else if rel == ".gitattributes" {
                if text() == GITATTRIBUTES.as_bytes() { Ok(()) } else { Err("changed since rb wrote it".into()) }
            } else if records.contains(f) {
                match &tampered {
                    Ok(t) => match t.get(&led.rel(f)) {
                        Some(why) => Err(format!("{why}; rb keeps what it did not write as it is")),
                        None => Ok(()),
                    },
                    Err(m) => Err(format!("rb could not read the record ({m}), so it cannot tell this file is its own")),
                }
            } else {
                Err("not rb's: rb did not write it".into())
            };
            match verdict {
                Ok(()) => ours.push(f.clone()),
                Err(why) => {
                    kept += 1;
                    self.keep.push(Kept { path: shown(f), why });
                }
            }
        }
        if linked && ours.is_empty() && kept == 0 {
            return;
        }
        if let Some(l) = crate::skill::through_link(&dir, root).filter(|_| linked) {
            self.links.push((format!("{}/", shown(&dir)), crate::skill::link_note(&dir, root), crate::sys::slashed(&l.real)));
        }
        let records_n = ours.iter().filter(|f| records.contains(*f)).count();
        // in an order that a removal stopped midway leaves a record rb remove still knows as its own: the records go
        // first, the files that say they are rb's (project.yaml, then the log) last, and the ledger kept here after them
        let ledger = crate::owned::ledger_path(root).filter(|l| l.starts_with(&dir)).or_else(|| crate::owned::aside_path(root).filter(|l| l.is_file()));
        let rank = |f: &PathBuf| match crate::sources::rel_to(f, &dir).unwrap_or_default().as_str() {
            _ if Some(f) == ledger.as_ref() => 5,
            "log.jsonl" => 4,
            "project.yaml" => 3,
            ".gitignore" | ".gitattributes" | "merges.json" => 2,
            rel if local(rel) => 1,
            _ => 0,
        };
        let mut ordered = ours.clone();
        ordered.sort_by_key(|f| rank(f));
        let acts = ordered
            .into_iter()
            .map(|f| if Some(&f) == ledger.as_ref() { Act::Ledger(f) } else { Act::File(f) })
            .chain(dirs.iter().filter(|d| !(linked && **d == dir)).cloned().map(Act::Empty))
            .collect();
        self.record_path = format!("{}/", shown(&dir));
        self.remove.insert(
            0,
            Item {
                path: self.record_path.clone(),
                why: format!(
                    "the record ({records_n} {}, each as its log says rb wrote it, and the log), and rb's own files and local copies{}",
                    if records_n == 1 { "record" } else { "records" },
                    if kept > 0 { format!(" ({kept} other {} kept, below)", if kept == 1 { "file" } else { "files" }) } else { String::new() }
                ) + if linked { "; the link and the folder it points to stay" } else { "" },
                acts,
            },
        );
        // the record's own files a commit is to keep: those not in git's index, or changed from the commit (staged too)
        let kept_by_git: Vec<&PathBuf> = ours.iter().filter(|f| !local(&crate::sources::rel_to(f, &dir).unwrap_or_default())).collect();
        if !in_git {
            self.uncommitted = kept_by_git.len();
        } else {
            let rel = crate::sources::rel_to(&dir, top).unwrap_or_else(|| DIR.to_string());
            let listed = |args: &[&str]| -> HashSet<String> {
                crate::sources::git(top, args).unwrap_or_default().split('\0').filter(|s| !s.is_empty()).map(str::to_string).collect()
            };
            let tracked = listed(&["ls-files", "-z", "--", &rel]);
            let changed: HashSet<String> = listed(&["status", "--porcelain=v1", "-uno", "-z", "--", &rel]).into_iter().filter_map(|e| e.get(3..).map(str::to_string)).collect();
            self.uncommitted = kept_by_git.iter().filter(|f| crate::sources::rel_to(f, top).map_or(true, |r| !tracked.contains(&r) || changed.contains(&r))).count();
        }
        if self.uncommitted > 0 {
            let id = led.project().map(|p| p.id).unwrap_or_else(|_| "record".into());
            let copy = root.parent().unwrap_or(root).join(format!("{id}-record.zip"));
            self.copy_first = format!("rb export --with-record --zip --out {}", crate::cli::shlex_quote(&crate::sys::slashed(&copy)));
        }
    }

    /// The agents' instruction files rb installed here, as this checkout's ownership ledger keeps them: each that checks
    /// out as rb wrote it goes, rb's part of a file that was there is cut out by its own bytes, and a file changed since
    /// is kept, with the lines to delete. rb's text that the ledger has no line for (a teammate's commit) is kept.
    fn integrations(&mut self, root: &Path, lines: &[Dict], shown: &dyn Fn(&Path) -> String) {
        let text = crate::skill::skill_text();
        let skill = crate::skill::claude_path(true, Some(root));
        let skill_dir = skill.parent().map(Path::to_path_buf);
        let cursor = crate::skill::cursor_path(Some(root));
        // each one a link takes elsewhere is said with the file it lands in
        for p in [&skill, &cursor, &root.join("AGENTS.md"), &root.join(".mcp.json")] {
            if let Some(l) = crate::skill::through_link(p, root) {
                self.links.push((shown(p), crate::skill::link_note(p, root), crate::sys::slashed(&l.real)));
            }
        }
        // the skill every project reads (a checkout at the home directory, or a .claude/ linked there) goes only where
        // this checkout wrote it for itself: by rb init, or by a write named by this checkout's own path (rb skill add
        // --project through the link); one rb skill add wrote for every project is listed and never taken
        let home_named = crate::skill::claude_path(false, None);
        let home_skill = crate::sources::resolve_path(&home_named);
        let at_home = crate::sources::resolve_path(&skill) == home_skill;
        let for_every_project = |p: &str| p == crate::sys::slashed(&home_named) || p == crate::sys::slashed(&home_skill);
        let by_init = crate::owned::last_for(lines, &skill, &["created", "replaced"])
            .map_or(false, |l| matches!(line_str(&l, "command"), Some("init" | "reconcile")) || line_str(&l, "path").map_or(false, |p| !for_every_project(p)));
        if !at_home || by_init {
            let before = self.remove.len() + self.keep.len();
            self.own_file(&skill, text.as_bytes(), lines, "rb's skill for Claude Code", skill_dir.into_iter().map(Act::Empty).collect(), shown);
            self.home_skill_named = at_home && self.remove.len() + self.keep.len() > before;
        }
        self.own_file(&cursor, crate::skill::cursor_text(&text).as_bytes(), lines, "rb's rule for Cursor", Vec::new(), shown);
        self.agents_md(root, lines, shown);
        self.mcp_json(root, lines, shown);
    }

    fn own_file(&mut self, path: &Path, rb_text: &[u8], lines: &[Dict], what: &str, mut more: Vec<Act>, shown: &dyn Fn(&Path) -> String) {
        let Ok(bytes) = std::fs::read(path) else { return };
        let line = crate::owned::last_for(lines, path, &["created", "replaced"]);
        let now = sha(&bytes);
        let as_rb_left = line.as_ref().and_then(|l| line_str(l, "sha256_after")) == Some(now.as_str());
        if let Some(l) = line.as_ref().filter(|l| as_rb_left && line_str(l, "how") == Some("replaced")) {
            // rb's text in place of a file that was there: that file goes back, byte for byte and with its time
            match line_str(l, "before").filter(|b| line_str(l, "sha256_before") == Some(sha(b.as_bytes()).as_str())) {
                Some(back) => self.remove.push(Item {
                    path: shown(path),
                    why: format!("{what}, which rb put in place of the file that was there: that file goes back to what it was, byte for byte"),
                    acts: vec![Act::Write(path.to_path_buf(), back.to_string(), l.get("mtime_before").and_then(Value::as_i64))],
                }),
                None => self.keep.push(Kept { path: shown(path), why: format!("{what}, in place of a file that was there, which rb cannot put back: delete it yourself if you want it gone") }),
            }
            return;
        }
        if line.is_none() {
            if bytes == rb_text {
                self.keep.push(Kept { path: shown(path), why: format!("{what}, as rb writes it, but rb did not write it here, so it stays: delete it yourself if you want it gone") });
            }
            return;
        }
        if as_rb_left {
            let mut acts = vec![made_at(path)];
            acts.append(&mut more);
            acts.extend(made_dirs(line.as_ref()));
            self.remove.push(Item { path: shown(path), why: format!("{what}, as rb wrote it{}", link_stays(path)), acts });
        } else {
            self.keep.push(Kept { path: shown(path), why: format!("{what}, changed since rb wrote it: delete it yourself if you want it gone") });
        }
    }

    /// rb's section of AGENTS.md: the file goes back to what it was while it is as rb left it; rb's section alone is cut
    /// out by its own bytes once the rest has changed (the section it took the place of put back); a section changed
    /// since is kept, with its lines.
    fn agents_md(&mut self, root: &Path, lines: &[Dict], shown: &dyn Fn(&Path) -> String) {
        let path = root.join("AGENTS.md");
        let Some(now) = std::fs::read(&path).ok().and_then(|b| String::from_utf8(b).ok()) else { return };
        let (begin, end) = (crate::skill::BEGIN, crate::skill::END);
        let span = crate::splice::section_span(&now, begin, end);
        let line = crate::owned::last_for(lines, &path, &["section", "created"]);
        let name = shown(&path);
        let lines_said = |(start, stop): (usize, usize)| {
            let (first, last) = crate::splice::lines_of(&now, &now[start..stop]).unwrap_or((0, 0));
            format!("lines {first} to {last}")
        };
        let Some(l) = line else {
            if let Some(s) = span {
                self.keep.push(Kept { path: name, why: format!("rb's section ({}): rb did not add it here, so it stays; delete those lines yourself if you want it gone", lines_said(s)) });
            }
            return;
        };
        let created = line_str(&l, "how") == Some("created");
        if line_str(&l, "sha256_after") == Some(sha(now.as_bytes()).as_str()) {
            if created {
                self.remove.push(Item { path: name, why: format!("made by rb, and as rb wrote it{}", link_stays(&path)), acts: vec![made_at(&path)] });
                return;
            }
            if let Some(back) = crate::owned::as_before(&now, &l) {
                let at = l.get("mtime_before").and_then(Value::as_i64);
                self.remove.push(Item { path: name, why: "rb's section, as rb added it: the file goes back to what it was, byte for byte".into(), acts: vec![Act::Write(path, back, at)] });
                return;
            }
        }
        // changed since: rb's section found by its own bytes, once
        let section = line_str(&l, "section").filter(|s| !s.is_empty() && now.matches(*s).count() == 1);
        if let Some(sec) = section {
            let start = now.find(sec).unwrap_or(0);
            let stop = start + sec.len();
            let replaced = line_str(&l, "replaced").unwrap_or("");
            let (back, why) = if replaced.is_empty() {
                (crate::splice::cut_span(&now, start, stop), "rb's section, as rb wrote it; the rest of the file, changed since, stays")
            } else {
                (format!("{}{replaced}{}", &now[..start], &now[stop..]), "rb's section, as rb wrote it, and the section it took the place of goes back; the rest of the file, changed since, stays")
            };
            let act = if created && back.trim().is_empty() { made_at(&path) } else { Act::Write(path, back, None) };
            self.remove.push(Item { path: name, why: why.into(), acts: vec![act] });
        } else if let Some(s) = span {
            self.keep.push(Kept { path: name, why: format!("rb's section ({}) was changed since rb wrote it: delete those lines yourself if you want it gone", lines_said(s)) });
        } else if now.contains(begin) || now.contains(end) {
            self.keep.push(Kept { path: name, why: "rb's section is not in it as rb wrote it, and rb's markers are not each on a line of their own once: delete rb's part yourself if you want it gone".into() });
        }
    }

    /// rb's server in .mcp.json, as the AGENTS.md section is: the file back as it was while it is as rb left it, else
    /// rb's member alone cut out when it is as rb writes it.
    fn mcp_json(&mut self, root: &Path, lines: &[Dict], shown: &dyn Fn(&Path) -> String) {
        let path = root.join(".mcp.json");
        let Some(now) = std::fs::read(&path).ok().and_then(|b| String::from_utf8(b).ok()) else { return };
        let Ok(cfg) = crate::pyjson::loads(&now) else { return };
        if cfg.get("mcpServers").and_then(|s| s.get("rabbit-brain")).is_none() {
            return;
        }
        let line = crate::owned::last_for(lines, &path, &["mcp_entry", "created"]);
        let created = line.as_ref().and_then(|l| line_str(l, "how")) == Some("created");
        let name = shown(&path);
        if line.is_none() {
            self.keep.push(Kept { path: name, why: "its \"rabbit-brain\" server: rb did not add it here, so it stays; delete it from mcpServers yourself if you want it gone".into() });
            return;
        }
        if let Some(l) = &line {
            if line_str(l, "sha256_after") == Some(sha(now.as_bytes()).as_str()) {
                if created {
                    self.remove.push(Item { path: name, why: format!("made by rb, and as rb wrote it{}", link_stays(&path)), acts: vec![made_at(&path)] });
                    return;
                }
                if let Some(back) = crate::owned::as_before(&now, l) {
                    let at = l.get("mtime_before").and_then(Value::as_i64);
                    self.remove.push(Item { path: name, why: "rb's server, as rb added it: the file goes back to what it was, byte for byte".into(), acts: vec![Act::Write(path, back, at)] });
                    return;
                }
            }
        }
        match crate::splice::mcp_cut(&now, "rabbit-brain", &crate::skill::mcp_server()) {
            Some(cut) => {
                let empty = crate::pyjson::loads(&cut).ok() == crate::pyjson::loads("{\"mcpServers\": {}}").ok();
                let act = if created && empty { made_at(&path) } else { Act::Write(path, cut, None) };
                self.remove.push(Item { path: name, why: "rb's server (\"rabbit-brain\" in mcpServers), as rb writes it; the rest of the file stays".into(), acts: vec![act] });
            }
            None => self.keep.push(Kept {
                path: name,
                why: "its \"rabbit-brain\" server is not as rb writes it: delete it from mcpServers yourself if you want it gone".into(),
            }),
        }
    }

    /// rb page's and rb export's own outputs at their defaults, beside .rb/; one written where it was asked (--out) is
    /// named and kept while it is as rb wrote it. A file the ledger keeps is rb's only with the bytes the ledger keeps;
    /// rb's own marks say so only of one the ledger has no line for (an older rb's).
    fn outputs(&mut self, root: &Path, lines: &[Dict], shown: &dyn Fn(&Path) -> String) {
        let kept = |p: &Path| crate::owned::last_for(lines, p, &["output"]).is_some();
        let page = root.join("rb-page.html");
        if page.is_file() {
            let ours = if kept(&page) { crate::owned::as_rb_wrote(root, &page) } else { crate::export::is_rb_page(&page) };
            if ours {
                self.remove.push(Item { path: shown(&page), why: "rb's page, as rb page wrote it".into(), acts: vec![Act::File(page.clone())] });
            } else {
                let why = if kept(&page) { "rb's page, changed since rb page wrote it: delete it yourself if you want it gone" } else { "not rb's page as rb page wrote it" };
                self.keep.push(Kept { path: shown(&page), why: why.into() });
            }
        }
        let dir = root.join("rb-export");
        if dir.is_dir() {
            if rb_export_as_written(&dir) {
                self.remove.push(Item { path: format!("{}/", shown(&dir)), why: "rb's export, every file as its manifest says".into(), acts: folder_acts(&dir, Some("manifest.json")) });
            } else {
                self.keep.push(Kept { path: format!("{}/", shown(&dir)), why: "not an rb export as rb wrote it (its files differ from its manifest)".into() });
            }
        }
        let zip = root.join("rb-export.zip");
        if zip.is_file() {
            let ours = if kept(&zip) { crate::owned::as_rb_wrote(root, &zip) } else { crate::export::is_rb_zip(&zip) };
            if ours {
                self.remove.push(Item { path: shown(&zip), why: "rb's export, as rb export wrote it".into(), acts: vec![Act::File(zip.clone())] });
            } else {
                let why = if kept(&zip) { "rb's export, changed since rb export wrote it: delete it yourself if you want it gone" } else { "not an rb export as rb wrote it" };
                self.keep.push(Kept { path: shown(&zip), why: why.into() });
            }
        }
        // rb tour's own folder: a repository of its own, taken only while every file is as the tour left it
        let mut tours: Vec<PathBuf> = Vec::new();
        for l in lines.iter().rev().filter(|l| line_str(l, "how") == Some("dir") && line_str(l, "kind") == Some("tour")) {
            let p = PathBuf::from(line_str(l, "path").unwrap_or(""));
            if !p.is_dir() || tours.contains(&p) {
                continue;
            }
            tours.push(p.clone());
            // every file there as the tour left it (one a removal stopped midway took is not missed), and no other
            let as_left = l.get("files").and_then(Value::as_dict).map_or(false, |f| crate::owned::tree_sums(&p).iter().all(|(k, v)| f.get(k) == Some(v)));
            if as_left {
                self.remove.push(Item { path: format!("{}/", shown(&p)), why: "rb tour's practice project, every file as the tour left it".into(), acts: folder_acts(&p, None) });
            } else {
                self.keep.push(Kept { path: format!("{}/", shown(&p)), why: "rb tour's practice project, a repository of its own, changed since the tour made it: delete the folder yourself if you want it gone".into() });
            }
        }
        let defaults = [page, dir, zip];
        let mut asked: Vec<String> = Vec::new();
        for l in lines.iter().filter(|l| line_str(l, "how") == Some("output")) {
            let p = PathBuf::from(line_str(l, "path").unwrap_or(""));
            let still = if p.is_dir() { rb_export_as_written(&p) } else { crate::owned::as_rb_wrote(root, &p) };
            if still && !defaults.iter().any(|d| crate::sources::resolve_path(d) == crate::sources::resolve_path(&p)) && !asked.contains(&crate::sys::slashed(&p)) {
                asked.push(crate::sys::slashed(&p));
                self.keep.push(Kept { path: shown(&p), why: "written where it was asked (--out): rb remove takes only its outputs at their defaults".into() });
            }
        }
    }

    /// What the clone holds for rb (the refs that keep runs' code, rb's own folder in .git, rb ui's mark of what was
    /// seen) goes only when no other checkout of the clone holds a record: else each of those is named, with its command.
    fn clone_wide(&mut self, root: &Path, top: &Path, shown: &dyn Fn(&Path) -> String) {
        let Some(common) = crate::owned::common_dir(root) else { return };
        let rel = crate::sources::rel_to(root, top).unwrap_or_default();
        for w in crate::elsewhere::worktrees(top) {
            if w.path == *top || (w.gone && !w.locked) {
                continue;
            }
            let place = crate::elsewhere::label(w.branch.as_deref(), &w.listed, w.head.as_deref());
            let there = format!("cd {} && rb remove", crate::cli::shlex_quote(&w.listed));
            if w.gone {
                // git keeps a locked worktree whose folder is away; rb cannot read its record, so it counts as one
                self.others.push(Kept { path: format!("{place} (locked, its folder not here)"), why: there });
                continue;
            }
            let theirs = if rel.is_empty() { w.path.clone() } else { w.path.join(&rel) };
            if theirs.join(DIR).join("project.yaml").is_file() || w.path.join(DIR).join("project.yaml").is_file() {
                self.others.push(Kept { path: place, why: there });
            }
        }
        let refs: Vec<String> = crate::sources::git(top, &["for-each-ref", "--format=%(refname)", "refs/rb/"]).unwrap_or_default().lines().map(str::to_string).filter(|r| !r.is_empty()).collect();
        let home = common.join("rb");
        let seen = common.join("rb-last-seen.json");
        if !self.others.is_empty() {
            let why = "shared by every checkout of this repository, and another one holds a record (below): rb remove in the last checkout that holds one takes it";
            if !refs.is_empty() {
                self.keep.push(Kept { path: format!("refs/rb/ ({} {})", refs.len(), if refs.len() == 1 { "ref" } else { "refs" }), why: why.into() });
            }
            // rb's folder said as a folder, as Will remove says it
            for (_, said) in [(&home, format!("{}/", shown(&home))), (&seen, shown(&seen))].into_iter().filter(|(p, _)| p.exists()) {
                self.keep.push(Kept { path: said, why: why.into() });
            }
            return;
        }
        if !refs.is_empty() {
            self.residue = residue(top, &common);
            let mut acts: Vec<Act> = refs.iter().cloned().map(Act::Ref).collect();
            // the folders of rb's refs, and of their reflogs (git keeps those when core.logAllRefUpdates is always)
            for folder in [common.join("refs").join("rb"), common.join("logs").join("refs").join("rb")] {
                acts.extend(walk(&folder).1.into_iter().filter(|_| folder.is_dir()).map(Act::Empty));
            }
            self.remove.push(Item {
                path: format!("refs/rb/ ({} {})", refs.len(), if refs.len() == 1 { "ref" } else { "refs" }),
                why: "the refs rb made to keep the code of recorded runs".into(),
                acts,
            });
        }
        if home.is_dir() {
            self.remove.push(Item { path: format!("{}/", shown(&home)), why: "rb's folder inside git's: its journal, its list of what it wrote outside .rb/, and the past versions it showed".into(), acts: vec![Act::Ledger(home)] });
        }
        if seen.is_file() {
            self.remove.push(Item { path: shown(&seen), why: "rb ui's mark of what you last saw".into(), acts: vec![Act::File(seen)] });
        }
    }

    /// What git will show once the plan is done: each file history keeps that it deletes (in .rb/, an instruction file,
    /// an output), and each tracked file it writes back whose bytes then differ from what git holds for it. A file git's
    /// index holds staged with rb's writes (added with git add, not committed) is named apart, with the command that
    /// unstages it: rb does not touch the index, and the next commit would add rb's writes to history.
    fn count_tracked(&mut self, top: &Path, cwd: &Path, lines: &[Dict]) {
        let names = |args: &[&str]| -> HashSet<String> { crate::sources::git(top, args).unwrap_or_default().split('\0').filter(|s| !s.is_empty()).map(str::to_string).collect() };
        let tracked = names(&["ls-files", "-z"]);
        let has_head = crate::sources::git(top, &["rev-parse", "--verify", "-q", "HEAD"]).map_or(false, |h| !h.trim().is_empty());
        let (in_head, staged_all) = if has_head {
            let in_head = names(&["ls-tree", "-r", "-z", "--name-only", "--full-tree", "HEAD"]);
            let mut staged = names(&["diff", "--cached", "--name-only", "--no-renames", "-z", "HEAD"]);
            // an entry git add -N made is in the index and in no commit: staged, though git diff --cached leaves it out
            staged.extend(tracked.iter().filter(|t| !in_head.contains(*t)).cloned());
            (in_head, staged)
        } else {
            (HashSet::new(), tracked.clone())
        };
        let rel = |p: &Path| crate::sources::rel_to(&crate::sources::resolve_path(p), top);
        let (mut deleted, mut changed) = (0, 0);
        let mut staged: Vec<String> = Vec::new();
        // a file rb writes back that was staged as it stood: git add puts it in the index without rb's part, as before rb
        let mut add_back: Vec<String> = Vec::new();
        // one staged in part, rb's part with what may be the user's own staging: named, with no command that would take
        // out either
        let mut in_part: Vec<String> = Vec::new();
        // whether one of those staged is a file rb writes back, which stays in the working tree
        let mut stays = false;
        // the sha256 of a file's bytes in git's index (`:path`) or a commit (`HEAD:path`), as the working tree has them
        let bytes_of = |spec: &str| -> Option<String> {
            crate::sources::run("git", &["-C", &top.to_string_lossy(), "cat-file", "--filters", spec], None, &[], None, std::time::Duration::from_secs(30))
                .filter(|o| o.code == 0)
                .map(|o| sha(&o.stdout))
        };
        // whether the index holds the last commit's bytes (none, where no commit holds the file) with rb's own writes
        // alone, as the ledger keeps each write's bytes before and after: unstaging it then takes out only rb's part
        let rb_alone = |r: &str, p: &Path, in_commit: bool| -> bool {
            let (Some(mut at), head) = (bytes_of(&format!(":{r}")), if in_commit { bytes_of(&format!("HEAD:{r}")) } else { None }) else { return false };
            if in_commit && head.is_none() {
                return false;
            }
            let mine = crate::owned::all_for(lines, p, &["created", "replaced", "section", "mcp_entry"]);
            for _ in 0..=mine.len() {
                if head.as_deref() == Some(at.as_str()) {
                    return true;
                }
                let Some(l) = mine.iter().rev().find(|l| line_str(l, "sha256_after") == Some(at.as_str())) else { return false };
                match line_str(l, "sha256_before") {
                    Some(b) => at = b.to_string(),
                    // rb made the file: it is in no commit
                    None => return !in_commit,
                }
            }
            false
        };
        let top_s = top.to_string_lossy().into_owned();
        let blob_of = |r: &str, bytes: Option<&[u8]>| -> Option<String> {
            let args: Vec<String> = match bytes {
                Some(_) => vec!["-C".into(), top_s.clone(), "hash-object".into(), format!("--path={r}"), "--stdin".into()],
                None => vec!["-C".into(), top_s.clone(), "hash-object".into(), "--".into(), r.to_string()],
            };
            let args: Vec<&str> = args.iter().map(String::as_str).collect();
            crate::sources::run("git", &args, None, &[], bytes, std::time::Duration::from_secs(30)).filter(|o| o.code == 0).map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        };
        let mut gone = |r: &str, staged: &mut Vec<String>| {
            deleted += in_head.contains(r) as usize;
            if tracked.contains(r) && staged_all.contains(r) {
                staged.push(r.to_string());
            }
        };
        for act in self.remove.iter().flat_map(|i| i.acts.iter()) {
            match act {
                Act::File(p) | Act::Ledger(p) if !p.is_dir() => {
                    if let Some(r) = rel(p) {
                        gone(&r, &mut staged);
                    }
                }
                Act::Ledger(p) => {
                    if let Some(r) = rel(p) {
                        let under: Vec<String> = tracked.iter().chain(in_head.iter()).filter(|t| t.starts_with(&format!("{r}/"))).cloned().collect::<std::collections::BTreeSet<_>>().into_iter().collect();
                        for t in under {
                            gone(&t, &mut staged);
                        }
                    }
                }
                Act::Write(p, text, _) => {
                    let Some(r) = rel(p).filter(|r| tracked.contains(r)) else { continue };
                    // the blob the written bytes make, through the repository's own filters, against the index's
                    let would = blob_of(&r, Some(text.as_bytes()));
                    let held = crate::sources::git(top, &["rev-parse", &format!(":{r}")]).map(|h| h.trim().to_string());
                    if would == held {
                        continue;
                    }
                    if !staged_all.contains(&r) {
                        changed += 1;
                    } else if blob_of(&r, None) == held {
                        add_back.push(r);
                    } else {
                        let in_commit = crate::sources::git(top, &["rev-parse", "--verify", "-q", &format!("HEAD:{r}")]).map(|h| h.trim().to_string());
                        changed += (in_commit.is_some() && in_commit != would) as usize;
                        if rb_alone(&r, p, in_commit.is_some()) {
                            stays = true;
                            staged.push(r);
                        } else {
                            in_part.push(r);
                        }
                    }
                }
                _ => {}
            }
        }
        self.tracked = deleted;
        self.changed = changed;
        staged.sort();
        staged.dedup();
        let mut cmds: Vec<String> = Vec::new();
        if !staged.is_empty() {
            // a folder of rb's (the record, an export) as one path, where every entry the index stages under it is rb's
            let folders: Vec<String> = self
                .remove
                .iter()
                .filter(|i| i.path.ends_with('/') && i.acts.iter().any(|a| matches!(a, Act::File(_))))
                .map(|i| i.path.trim_end_matches('/').to_string())
                .collect();
            let whole = |f: &str| !staged_all.iter().any(|t| t.starts_with(&format!("{f}/")) && tracked.contains(t) && !staged.contains(t));
            let mut specs: Vec<String> = Vec::new();
            for r in &staged {
                let spec = folders.iter().find(|f| r.starts_with(&format!("{f}/")) && whole(f)).cloned().unwrap_or_else(|| r.clone());
                if !specs.contains(&spec) {
                    specs.push(spec);
                }
            }
            let quoted: Vec<String> = specs.iter().map(|s| crate::cli::shlex_quote(s)).collect();
            // before a first commit, git rm keeps in the index a file whose index differs from the file there, unless forced
            let force = if stays { "-f " } else { "" };
            cmds.push(if has_head { format!("git restore --staged -- {}", quoted.join(" ")) } else { format!("git rm -r -q {force}--cached -- {}", quoted.join(" ")) });
        }
        if !add_back.is_empty() {
            cmds.push(format!("git add -- {}", add_back.iter().map(|r| crate::cli::shlex_quote(r)).collect::<Vec<_>>().join(" ")));
        }
        if !cmds.is_empty() {
            let at_top = crate::sources::resolve_path(cwd) == crate::sources::resolve_path(top);
            let cmd = cmds.join(" && ");
            self.unstage = if at_top { cmd } else { format!("cd {} && {cmd}", crate::cli::shlex_quote(&crate::sys::slashed(top))) };
        }
        in_part.sort();
        in_part.dedup();
        staged.extend(add_back);
        staged.extend(in_part.iter().cloned());
        staged.sort();
        self.staged = staged;
        self.staged_in_part = in_part;
    }

    /// What rb wrote for this machine, not this checkout: named, never removed.
    fn listed(&mut self) {
        let skill = crate::skill::claude_path(false, None);
        if let Some(dir) = skill.parent().filter(|d| d.is_dir() && !self.home_skill_named) {
            self.listed.push(Kept { path: home_shown(dir), why: "rb's skill for every project, from rb skill add: delete it yourself if you want it gone".into() });
        }
        let config = PathBuf::from(crate::hub::config_dir());
        if config.is_dir() {
            self.listed.push(Kept { path: home_shown(&config), why: "rb's sign-in and usage settings on this machine (rb logout, rb telemetry disable)".into() });
        }
        self.listed.push(Kept { path: "the rb command itself".into(), why: "pip uninstall rabbit-brain (or uv tool uninstall rabbit-brain, pipx uninstall rabbit-brain)".into() });
    }

    /// Where a link takes the file the plan names `path`, as its text says it after the path: "" for most.
    fn link_of(&self, path: &str) -> String {
        self.links.iter().find(|(p, _, _)| p == path).map(|(_, note, _)| note.clone()).unwrap_or_default()
    }

    pub fn to_value(&self, dry_run: bool) -> Dict {
        // a file a link takes elsewhere also gives the file it lands in (`real_path`)
        let entry = |path: &str, why: &str| {
            let mut d = crate::dict! {"path" => path, "why" => why};
            if let Some((_, _, real)) = self.links.iter().find(|(p, _, _)| p == path) {
                d.insert("real_path".into(), Value::Str(real.clone()));
            }
            Value::Dict(d)
        };
        let items = |v: &[Kept]| Value::List(v.iter().map(|k| entry(&k.path, &k.why)).collect());
        crate::dict! {
            "root" => crate::sys::slashed(&self.root),
            "dry_run" => dry_run,
            "remove" => Value::List(self.remove.iter().map(|i| entry(&i.path, &i.why)).collect()),
            "keep" => items(&self.keep),
            "untouched" => UNTOUCHED,
            "residue" => Value::list_of_str(self.residue.clone()),
            "listed" => items(&self.listed),
            "others" => Value::List(self.others.iter().map(|k| crate::obj! {"place" => k.path.clone(), "command" => k.why.clone()}).collect()),
            "uncommitted" => self.uncommitted as i64,
            "copy_first" => self.copy_first.clone(),
            "tracked_deleted" => self.tracked as i64,
            "tracked_changed" => self.changed as i64,
            "staged" => Value::list_of_str(self.staged.clone()),
            "unstage" => self.unstage.clone(),
            "staged_in_part" => Value::list_of_str(self.staged_in_part.clone())
        }
    }

    /// The plan in rb's words.
    pub fn said(&self) -> Vec<String> {
        let mut out = Vec::new();
        if self.remove.is_empty() {
            out.push("Nothing of rb's to remove here.".into());
        } else {
            out.push("Will remove:".into());
            out.extend(self.remove.iter().map(|i| format!("  {}{}: {}", i.path, self.link_of(&i.path), i.why)));
        }
        if !self.keep.is_empty() {
            out.push("Will keep:".into());
            out.extend(self.keep.iter().map(|k| format!("  {}{}: {}", k.path, self.link_of(&k.path), k.why)));
        }
        if !self.others.is_empty() {
            out.push("Other checkouts of this repository hold a record, each removed by rb remove run there:".into());
            out.extend(self.others.iter().map(|k| format!("  {}: {}", k.path, k.why)));
        }
        out.push(UNTOUCHED.into());
        if !self.residue.is_empty() {
            let shown: Vec<String> = self.residue.iter().take(5).map(|o| o.chars().take(12).collect()).collect();
            out.push(format!(
                "  {} git {} that only rb's refs reached {} in .git until git's own gc removes {}: {}{}",
                self.residue.len(),
                if self.residue.len() == 1 { "object" } else { "objects" },
                if self.residue.len() == 1 { "stays" } else { "stay" },
                if self.residue.len() == 1 { "it" } else { "them" },
                shown.join(", "),
                if self.residue.len() > 5 { ", ..." } else { "" }
            ));
        }
        out.push("Listed, not removed:".into());
        out.extend(self.listed.iter().map(|k| format!("  {}: {}", k.path, k.why)));
        if self.uncommitted > 0 {
            let one = self.uncommitted == 1;
            let said = if self.in_git {
                if one { "has uncommitted changes" } else { "have uncommitted changes" }
            } else if one {
                "is in no commit, since this folder is not in git"
            } else {
                "are in no commit, since this folder is not in git"
            };
            out.push(format!("{} {} of the record {said}. To keep a copy first, run: {}", self.uncommitted, if one { "file" } else { "files" }, self.copy_first));
        }
        let files = |n: usize| if n == 1 { "file" } else { "files" };
        match (self.tracked, self.changed) {
            (0, 0) => {}
            (d, 0) => out.push(format!("git will show {d} {} deleted; history keeps them.", files(d))),
            (0, c) => out.push(format!("git will show {c} {} changed; history keeps them as they were.", files(c))),
            (d, c) => out.push(format!("git will show {d} {} deleted and {c} changed; history keeps them as they were.", files(d))),
        }
        if !self.staged.is_empty() {
            out.push(self.staged_said(false));
        }
        out
    }

    /// The files git's index holds staged with rb's writes, and the command that unstages them (`done`: rb remove ran);
    /// one staged in part is named apart, with no command.
    fn staged_said(&self, done: bool) -> String {
        let after = if done { "" } else { "after rb remove, " };
        let n = self.staged.len() - self.staged_in_part.len();
        let mut said: Vec<String> = Vec::new();
        if n > 0 {
            said.push(format!(
                "{n} staged {} {} rb's writes, and rb does not touch git's index: {after}run {}, or your next commit adds them to history.",
                if n == 1 { "file" } else { "files" },
                if n == 1 { "holds" } else { "hold" },
                self.unstage
            ));
        }
        if let [first, ..] = self.staged_in_part.as_slice() {
            let one = self.staged_in_part.len() == 1;
            let named = if one { first.clone() } else { crate::assess::first_three(&self.staged_in_part, ", ") };
            said.push(format!(
                "{named} {} staged in part, rb's part with other changes, and no one command takes out only rb's: {after}stage {} again as you want {}, or your next commit adds rb's part to history.",
                if one { "is" } else { "are" },
                if one { "it" } else { "them" },
                if one { "it" } else { "them" }
            ));
        }
        said.join(" ")
    }

    /// When an item runs: what needs rb's ledger first (the instruction files, the outputs), then the refs, then the
    /// record, then rb's folder in git's, which holds the ledger.
    fn stage(&self, item: &Item) -> u8 {
        let rec = self.root.join(DIR);
        item.acts
            .iter()
            .map(|a| match a {
                Act::Ledger(p) if !p.starts_with(&rec) => 3,
                Act::File(p) | Act::Empty(p) | Act::Ledger(p) | Act::Write(p, _, _) if p.starts_with(&rec) => 2,
                Act::Ref(_) => 1,
                _ => 0,
            })
            .max()
            .unwrap_or(0)
    }

    /// Do what the plan says: files written back, files and folders taken away, refs deleted, the folders rb made taken
    /// away once empty, each item stopping at its first failure, and rb's ledger last of all. What was removed, in the
    /// plan's order, and what could not be, with the system's words for why.
    fn run(&self) -> (Vec<String>, Vec<Kept>) {
        let top = crate::sources::git_top(&self.root).unwrap_or_else(|| self.root.clone());
        let mut order: Vec<usize> = (0..self.remove.len()).collect();
        order.sort_by_key(|&i| self.stage(&self.remove[i]));
        let mut why: Vec<Option<String>> = vec![None; self.remove.len()];
        let mut any_failed = false;
        for i in order {
            for act in &self.remove[i].acts {
                let gone = |r: std::io::Result<()>| r.or_else(|e| if e.kind() == std::io::ErrorKind::NotFound { Ok(()) } else { Err(e) }).map_err(|e| crate::sys::os_words(&e));
                let r: Result<(), String> = match act {
                    Act::File(p) => gone(std::fs::remove_file(p)),
                    Act::Ledger(_) if any_failed => Err("kept, since something above could not be removed: rb remove, run again, finishes with it".into()),
                    Act::Ledger(p) if p.is_dir() => gone(std::fs::remove_dir_all(p)),
                    Act::Ledger(p) => gone(std::fs::remove_file(p)),
                    Act::Write(p, text, at) => std::fs::write(p, text)
                        .and_then(|_| match at {
                            Some(ns) if *ns >= 0 => std::fs::File::options().write(true).open(p)?.set_modified(std::time::UNIX_EPOCH + std::time::Duration::from_nanos(*ns as u64)),
                            _ => Ok(()),
                        })
                        .map_err(|e| crate::sys::os_words(&e)),
                    Act::Ref(r) => crate::sources::git(&top, &["update-ref", "-d", r]).map(|_| ()).ok_or_else(|| format!("git could not delete {r}")),
                    // only an empty folder goes: one that still holds a file is kept, one left empty is a failure
                    Act::Empty(p) => match std::fs::remove_dir(p) {
                        Err(e) if e.kind() != std::io::ErrorKind::NotFound && std::fs::read_dir(p).map_or(false, |mut d| d.next().is_none()) => Err(crate::sys::os_words(&e)),
                        _ => Ok(()),
                    },
                };
                if let Err(e) = r {
                    why[i] = Some(e);
                    any_failed = true;
                    break;
                }
            }
        }
        let (mut removed, mut failed) = (Vec::new(), Vec::new());
        for (item, w) in self.remove.iter().zip(why) {
            match w {
                None => removed.push(item.path.clone()),
                Some(w) => failed.push(Kept { path: item.path.clone(), why: w }),
            }
        }
        crate::sources::forget_git();
        (removed, failed)
    }
}

/// Whether a folder is an rb export as rb wrote it: rb's manifest, every file there as it says (one a removal stopped
/// midway took is not missed), and no file it does not name.
fn rb_export_as_written(dir: &Path) -> bool {
    let marked = std::fs::read_to_string(dir.join("manifest.json")).ok().and_then(|t| crate::pyjson::loads(&t).ok()).map_or(false, |m| m.get("format").and_then(Value::as_str) == Some(crate::export::FORMAT));
    marked && crate::export::verify(dir).iter().all(|p| p.ends_with(": missing")) && crate::export::extra_files(dir).is_empty()
}

/// A folder of rb's taken away file by file, `last` (the file that says it is rb's) after the others, then its folders:
/// a removal stopped midway leaves what lets rb remove, run again, know the rest as rb's.
fn folder_acts(dir: &Path, last: Option<&str>) -> Vec<Act> {
    let (mut files, dirs) = walk(dir);
    files.sort_by_key(|f| last.map_or(false, |l| f.file_name().map_or(false, |n| n == l) && f.parent() == Some(dir)));
    files.into_iter().map(Act::File).chain(dirs.into_iter().map(Act::Empty)).collect()
}

/// A path under the home directory, said from it (`~/.claude/skills/rabbit-brain/`).
fn home_shown(p: &Path) -> String {
    let home = crate::sources::resolve_path(&crate::sys::home());
    match crate::sources::rel_to(&crate::sources::resolve_path(p), &home) {
        Some(r) => format!("~/{r}/"),
        None => format!("{}/", crate::sys::slashed(p)),
    }
}

/// The objects only rb's refs reach: what git reaches from every ref, its reflogs and its index, less what it reaches
/// without refs/rb/ and their reflogs (which git keeps when core.logAllRefUpdates is always). rb deletes none of them;
/// git's own gc does, in its time.
fn residue(top: &Path, common: &Path) -> Vec<String> {
    let ids = |out: Option<Vec<u8>>| -> HashSet<String> { String::from_utf8_lossy(&out.unwrap_or_default()).lines().filter_map(|l| l.split_whitespace().next().map(str::to_string)).collect() };
    let all = ids(crate::sources::git_bytes(top, &["rev-list", "--objects", "--all", "--reflog", "--indexed-objects"]));
    // every reflog but rb's refs', read from git's own files: each line's old and new commit
    let mut tips: Vec<String> = Vec::new();
    let rb_logs = common.join("logs").join("refs").join("rb");
    let mut logs: Vec<PathBuf> = walk(&common.join("logs")).0;
    if let Ok(rd) = std::fs::read_dir(common.join("worktrees")) {
        for e in rd.flatten() {
            logs.extend(walk(&e.path().join("logs")).0);
        }
    }
    for f in logs.iter().filter(|f| !f.starts_with(&rb_logs)) {
        for line in std::fs::read_to_string(f).unwrap_or_default().lines() {
            tips.extend(line.split_whitespace().take(2).filter(|s| s.len() >= 40 && s.chars().all(|c| c.is_ascii_hexdigit()) && s.chars().any(|c| c != '0')).map(str::to_string));
        }
    }
    let top_s = top.to_string_lossy().into_owned();
    let input = tips.join("\n") + "\n";
    let args = ["-C", top_s.as_str(), "rev-list", "--objects", "--exclude=refs/rb/*", "--all", "--indexed-objects", "--ignore-missing", "--stdin"];
    let without = ids(crate::sources::run("git", &args, None, &[], Some(input.as_bytes()), std::time::Duration::from_secs(120)).filter(|r| r.code == 0).map(|r| r.stdout));
    let mut only: Vec<String> = all.difference(&without).cloned().collect();
    only.sort();
    only
}

/// rb remove [--dry-run] [--yes]: the plan, then, as asked, what it says.
pub fn cmd_remove(ns: &crate::argparse::Namespace, out: &mut Out) -> Res<i32> {
    let dry = crate::cmds::flag(ns, "dry_run");
    let cwd = std::env::current_dir().unwrap_or_default();
    // refused where rb confirm is: an agent session (a CI job, a chat's leftover id), or RB_ACTOR naming an agent
    if !dry && crate::actor::agent_session_sign().is_some() {
        return Err(RBError::msg_fix(
            "E_USAGE",
            "Nothing was removed: this is an agent session, and taking rb out of a checkout is the user's to do.",
            "The user runs rb remove in their own terminal. rb remove --dry-run shows what it would take, and writes nothing.",
        ));
    }
    if !dry && !crate::actor::current(Some(&cwd))?.is_human() {
        return Err(RBError::msg_fix(
            "E_USAGE",
            "Nothing was removed: RB_ACTOR names an agent here, and taking rb out of a checkout is the user's to do.",
            "Unset RB_ACTOR, then run rb remove in your own terminal. rb remove --dry-run shows what it would take, and writes nothing.",
        ));
    }
    let plan = Plan::of(&cwd)?;
    let mut data = plan.to_value(dry);
    let shown_root = crate::sys::slashed(&plan.root);
    let said = plan.said();
    if dry {
        out.say(format!("Dry run: nothing was removed. rb remove would do this in {shown_root}:"));
        out.say_all(said);
        out.data = data;
        return Ok(0);
    }
    if plan.remove.is_empty() {
        out.say_all(said);
        data.insert("removed".into(), Value::List(Vec::new()));
        out.data = data;
        return Ok(0);
    }
    let yes = crate::cmds::flag(ns, "yes");
    if !yes {
        if out.json_mode || crate::cli::capturing() || !crate::cli::isatty(0) || !crate::cli::isatty(1) {
            return Err(RBError::msg_fix(
                "E_USAGE",
                "Nothing was removed: rb remove asks before it takes anything, and there is no terminal here to ask in.",
                "Run it in your own terminal, or give the yes up front: rb remove --yes. rb remove --dry-run shows what it would take.",
            ));
        }
        let mut before = vec![format!("rb remove would do this in {shown_root}:")];
        before.extend(said);
        if !crate::skill::ask_once(before, &[String::new()], "Remove what is under Will remove, and take rb's part out of the files named there? [y/N] ") {
            out.say("Nothing was removed.");
            data.insert("removed".into(), Value::List(Vec::new()));
            out.data = data;
            return Ok(0);
        }
    } else {
        out.say(format!("rb remove in {shown_root}:"));
        out.say_all(said);
    }
    let (removed, failed) = plan.run();
    // a file rb wrote back (rb's part taken out of it) is said so, not removed; .rb/ is said removed only once it is
    // gone: the files it keeps stay in it
    let written_back: HashSet<String> = plan.remove.iter().filter(|i| i.acts.iter().any(|a| matches!(a, Act::Write(..)))).map(|i| i.path.clone()).collect();
    let put_back: Vec<String> = removed.iter().filter(|p| written_back.contains(*p)).cloned().collect();
    let said_removed: Vec<String> = removed
        .iter()
        .filter(|p| !written_back.contains(*p))
        .map(|p| match p {
            _ if *p != plan.record_path || !plan.root.join(DIR).exists() => p.clone(),
            _ if is_link(&plan.root.join(DIR)) => format!("rb's files in {p} (the link and the folder it points to stay)"),
            _ => format!("rb's files in {p} (the files it keeps stay)"),
        })
        .collect();
    let mut done: Vec<String> = Vec::new();
    if !said_removed.is_empty() || put_back.is_empty() {
        done.push(format!("Removed {}.", if said_removed.is_empty() { "nothing".to_string() } else { said_removed.join(", ") }));
    }
    if !put_back.is_empty() {
        done.push(format!("Took rb's part out of {}.", put_back.join(", ")));
    }
    out.say("");
    if failed.is_empty() {
        out.say(format!("{} Nothing else was changed.", done.join(" ")));
    } else {
        out.say(done.join(" "));
        for f in &failed {
            out.say(format!("  could not remove {}: {}", f.path, f.why));
        }
    }
    if !plan.staged.is_empty() {
        out.say(plan.staged_said(true));
    }
    data.insert("removed".into(), Value::list_of_str(removed));
    data.insert("put_back".into(), Value::list_of_str(put_back));
    data.insert("failed".into(), Value::List(failed.iter().map(|k| crate::obj! {"path" => k.path.clone(), "why" => k.why.clone()}).collect()));
    out.data = data;
    if let Some(f) = failed.first() {
        return Err(RBError::msg_fix("E_WRITE_FAILED", format!("{} could not be removed: {}", f.path, f.why), "Check that it is yours to change, then run rb remove again: it takes only what is still there."));
    }
    Ok(0)
}
