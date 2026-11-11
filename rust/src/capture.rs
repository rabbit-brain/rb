//! The code a run's receipt kept, and how much of it: one reading of receipt.attached (the capture made when the run
//! was recorded) and receipt.produced (the code at the run's start, or given), for every view and every word.
//!
//! The keys a capture keeps beside the code it describes: `captured` (start, after, given), `kept` (whole, part, none),
//! `kept_why` (left_out for part; too_many, git_failed, git_timeout, list_failed, no_commit for none), `git_said`
//! (git's first line where git failed), `git_secs` (how long rb waited, where git did not answer), `listing` (changed
//! or untracked, the listing that failed), `left_out_files` (up to 20 files, each with its path, why and whether git
//! tracked it), `left_out_more` (the count of the rest) and `too_many` (files, folder, in_folder). `left_out` stays the
//! count every rb since 0.7 reads. A receipt an earlier rb wrote holds none of them, and reads as captured after the run
//! (given where it holds produced.tree), its `left_out` a count of files it does not name.

use crate::pyjson;
use crate::shell::strings::evidence;
use crate::value::{Dict, Value};
use std::path::Path;

/// At most this many left-out files are named on a receipt; the rest are counted.
pub const LEFT_OUT_NAMED: usize = 20;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum When {
    Start,
    After,
    Given,
}

impl When {
    pub fn word(self) -> &'static str {
        match self {
            When::Start => "start",
            When::After => "after",
            When::Given => "given",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kept {
    Whole,
    Part,
    None,
}

impl Kept {
    pub fn word(self) -> &'static str {
        match self {
            Kept::Whole => "whole",
            Kept::Part => "part",
            Kept::None => "none",
        }
    }
}

/// A file rb left out of a kept tree: over 10 MB, or not a regular file. A file git tracked is in the tree as the
/// commit holds it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LeftOut {
    pub path: String,
    pub why: String,
    pub tracked: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TooMany {
    pub files: i64,
    pub folder: String,
    pub in_folder: i64,
}

/// What a receipt's capture kept of the code.
#[derive(Debug, Clone, PartialEq)]
pub struct Capture {
    pub when: When,
    pub commit: Option<String>,
    /// the tree rb kept: of the uncommitted changes, or the commit's own
    pub tree: Option<String>,
    pub dirty: bool,
    pub kept: Kept,
    pub why: String,
    pub git_said: String,
    pub git_secs: String,
    pub listing: String,
    pub left_out: Vec<LeftOut>,
    pub left_out_more: i64,
    /// an earlier rb's count of left-out files, which it does not name
    pub unnamed: i64,
    pub too_many: Option<TooMany>,
    pub nested: i64,
    /// an earlier rb wrote it: it says nothing of when or how much it kept
    pub earlier: bool,
}

fn text(d: &Dict, k: &str) -> Option<String> {
    d.get(k).filter(|v| v.truthy()).map(pyjson::py_str).filter(|s| !s.is_empty())
}

fn int(d: &Dict, k: &str) -> i64 {
    d.get(k).and_then(Value::as_i64).unwrap_or(0)
}

impl Capture {
    /// A capture as a receipt's part keeps it (`attached`, or `produced` with its commit and tree): read the same way
    /// whichever rb wrote it. `when` is what it reads as where the part says nothing of when it was captured.
    pub fn of(d: &Dict, when: When) -> Capture {
        let when = match d.get("captured").and_then(Value::as_str) {
            Some("start") => When::Start,
            Some("given") => When::Given,
            Some("after") => When::After,
            _ => when,
        };
        let commit = text(d, "commit");
        let tree = text(d, "tree");
        let dirty = d.get("dirty").map_or(false, Value::truthy);
        let mut c = Capture {
            when,
            commit: commit.clone(),
            tree: tree.clone(),
            dirty,
            kept: Kept::Whole,
            why: text(d, "kept_why").unwrap_or_default(),
            git_said: text(d, "git_said").unwrap_or_default(),
            git_secs: d.get("git_secs").map(pyjson::py_str).unwrap_or_default(),
            listing: text(d, "listing").unwrap_or_default(),
            left_out: Vec::new(),
            left_out_more: int(d, "left_out_more"),
            unnamed: 0,
            too_many: None,
            nested: int(d, "nested"),
            earlier: d.get("kept").is_none(),
        };
        c.unnamed = int(d, "left_out");
        match d.get("left_out_files") {
            Some(Value::List(l)) => {
                c.left_out = l
                    .iter()
                    .filter_map(|x| x.as_dict())
                    .map(|x| LeftOut { path: text(x, "path").unwrap_or_default(), why: text(x, "why").unwrap_or_default(), tracked: x.get("tracked").map_or(false, Value::truthy) })
                    .collect();
                c.unnamed = (c.unnamed - c.left_out.len() as i64 - c.left_out_more).max(0);
            }
            _ => {}
        }
        if let Some(Value::Dict(t)) = d.get("too_many") {
            c.too_many = Some(TooMany { files: int(t, "files"), folder: text(t, "folder").unwrap_or_default(), in_folder: int(t, "in_folder") });
        }
        match d.get("kept").and_then(Value::as_str) {
            Some("part") => c.kept = Kept::Part,
            Some("none") => c.kept = Kept::None,
            Some(_) => c.kept = Kept::Whole,
            // an earlier rb's receipt: its state as that rb kept it
            None => {
                if d.is_empty() {
                    c.kept = Kept::None;
                    c.why = "not_in_git".into();
                } else if commit.is_none() && tree.is_none() {
                    c.kept = Kept::None;
                    c.why = "no_commit".into();
                } else if dirty && tree.is_none() {
                    c.kept = Kept::None;
                    if c.unnamed > 0 {
                        c.why = "too_many".into();
                        c.too_many = Some(TooMany { files: c.unnamed, folder: String::new(), in_folder: 0 });
                        c.unnamed = 0;
                    }
                } else if c.unnamed > 0 {
                    c.kept = Kept::Part;
                    c.why = "left_out".into();
                }
            }
        }
        c
    }

    /// The capture rb made when the run was recorded (receipt.attached); None-kept where the receipt holds none.
    pub fn attached(run: &crate::record::Run) -> Capture {
        Capture::of(run.receipt.attached.as_ref().unwrap_or(&Dict::new()), When::After)
    }

    /// The code the run names as its own in receipt.produced: captured at its start, or given (--commit, --tree). An
    /// earlier rb's produced reads as given where it holds a tree. None where it names no code.
    pub fn produced(run: &crate::record::Run) -> Option<Capture> {
        let p = &run.receipt.produced;
        let when = match p.get("captured").and_then(Value::as_str) {
            Some("start") => When::Start,
            Some("given") => When::Given,
            _ if text(p, "tree").is_some() => When::Given,
            _ => return None,
        };
        let mut c = Capture::of(p, when);
        if p.get("kept").is_none() {
            c.kept = Kept::Whole;
            c.why.clear();
        }
        Some(c)
    }

    /// The run's code, by the one rule: produced where it was captured at the start or given, else attached.
    pub fn of_run(run: &crate::record::Run) -> Capture {
        Capture::produced(run).unwrap_or_else(|| Capture::attached(run))
    }

    /// The code this capture names, (commit, tree): the kept tree of uncommitted changes, or the commit itself
    /// (tree == commit) where it had none. None where it kept no code or names no commit.
    pub fn code(&self) -> Option<(String, String)> {
        if self.kept == Kept::None {
            return None;
        }
        let commit = self.commit.clone()?;
        if self.dirty {
            return Some((commit, self.tree.clone()?));
        }
        Some((commit.clone(), commit))
    }

    /// Every file it left out, named or not.
    pub fn left_out_count(&self) -> i64 {
        self.left_out.len() as i64 + self.left_out_more + self.unnamed
    }

    /// The receipt's keys for this capture, as a write keeps them (beside commit, branch, dirty and the rest).
    pub fn keys(&self) -> Dict {
        let mut d = crate::dict! {"captured" => self.when.word(), "kept" => self.kept.word()};
        if !self.why.is_empty() && self.kept != Kept::Whole {
            d.insert("kept_why".into(), Value::Str(self.why.clone()));
        }
        if !self.git_said.is_empty() {
            d.insert("git_said".into(), Value::Str(self.git_said.clone()));
        }
        if !self.git_secs.is_empty() {
            let secs = self.git_secs.parse::<f64>().ok().map_or(Value::Str(self.git_secs.clone()), |f| if f.fract() == 0.0 { Value::Int(f as i64) } else { Value::Float(f) });
            d.insert("git_secs".into(), secs);
        }
        if !self.listing.is_empty() {
            d.insert("listing".into(), Value::Str(self.listing.clone()));
        }
        if !self.left_out.is_empty() {
            d.insert("left_out".into(), Value::Int(self.left_out_count()));
            d.insert(
                "left_out_files".into(),
                Value::List(self.left_out.iter().map(|l| crate::obj! {"path" => l.path.clone(), "why" => l.why.clone(), "tracked" => l.tracked}).collect()),
            );
        }
        if self.left_out_more > 0 {
            d.insert("left_out_more".into(), Value::Int(self.left_out_more));
        }
        if let Some(t) = &self.too_many {
            let mut tm = crate::dict! {"files" => t.files};
            if !t.folder.is_empty() {
                tm.insert("folder".into(), Value::Str(t.folder.clone()));
                tm.insert("in_folder".into(), Value::Int(t.in_folder));
            }
            d.insert("too_many".into(), Value::Dict(tm));
        }
        d
    }
}

/// Whether git here has a tree, and whether a ref under refs/rb/snapshots/ keeps it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Here {
    /// in this repository, kept by rb's ref
    Kept,
    /// in this clone, but no ref keeps it: git gc may remove it
    Unreffed,
    /// not in this clone
    Absent,
}

/// Whether a run's code as given (--commit, --tree) is code this repository does not have (B2): its tree, or, with no
/// tree, its commit. False for code rb captured itself, and for a run with no code given.
pub fn given_absent(root: &Path, run: &crate::record::Run) -> bool {
    let Some(c) = Capture::produced(run).filter(|c| c.when == When::Given) else { return false };
    match (&c.tree, &c.commit) {
        (Some(t), _) => tree_here(root, t) == Here::Absent,
        (None, Some(commit)) => crate::sources::git(root, &["cat-file", "-e", &format!("{commit}^{{commit}}")]).is_none(),
        (None, None) => false,
    }
}

pub fn tree_here(root: &Path, tree: &str) -> Here {
    if crate::sources::git(root, &["cat-file", "-e", &format!("{tree}^{{tree}}")]).is_none() {
        return Here::Absent;
    }
    if crate::sources::snapshot_ref_kept(root, tree) {
        Here::Kept
    } else {
        Here::Unreffed
    }
}

/// A count as rb prints one: 2005, no separators.
pub fn grouped(n: i64) -> String {
    n.to_string()
}

fn files_n(n: i64) -> String {
    format!("{} {}", grouped(n), if n == 1 { "file" } else { "files" })
}

fn trunc(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

fn reason(why: &str) -> String {
    if why == "not_regular" { evidence("not_regular", &[]) } else { evidence("over_10_mb", &[]) }
}

/// The files a part-kept capture lacks, named with why: "embeddings.bin (over 10 MB), data/link (not a regular
/// file)", and "and 3 more" for those past the named ones. A tracked file is in the tree as its commit holds it.
fn lacks(c: &Capture) -> String {
    let mut parts: Vec<String> = c.left_out.iter().filter(|l| !l.tracked).map(|l| format!("{} ({})", l.path, reason(&l.why))).collect();
    if c.left_out_more > 0 {
        parts.push(evidence("part_more", &[("count", grouped(c.left_out_more))]));
    }
    parts.join(", ")
}

/// The reasons of every file a capture left out, counted, as a page gives them: "1 over 10 MB, 1 not a regular file";
/// the files it does not name, by count alone.
fn reasons(c: &Capture) -> String {
    let mut rs: Vec<(String, i64)> = Vec::new();
    for l in &c.left_out {
        let r = reason(&l.why);
        match rs.iter_mut().find(|(x, _)| *x == r) {
            Some(x) => x.1 += 1,
            None => rs.push((r, 1)),
        }
    }
    let rest = c.left_out_more + c.unnamed;
    let mut said: Vec<String> = rs.into_iter().map(|(r, n)| format!("{n} {r}")).collect();
    if rest > 0 {
        said.push(format!("{rest} not named"));
    }
    said.join(", ")
}

/// What rb says of a capture that kept no code, or kept part of it, on a screen of the user's own (`page` false: rb's
/// text, rb open, rb ui) or a page (`page` true: no file named, no git message). Empty where it kept the whole code,
/// or where there was nothing to keep (not in git, no commit yet: the receipt's own lines say those).
pub fn state_lines(c: &Capture, page: bool) -> Vec<String> {
    match c.kept {
        Kept::Whole => Vec::new(),
        Kept::None => none_line(c, page).into_iter().collect(),
        Kept::Part => {
            let tree = trunc(c.tree.as_deref().unwrap_or(""), 12);
            if page {
                // a page names no file it did not cite: counts and reasons alone
                let untracked: Vec<&LeftOut> = c.left_out.iter().filter(|l| !l.tracked).collect();
                let tracked: Vec<&LeftOut> = c.left_out.iter().filter(|l| l.tracked).collect();
                let mut line = evidence("part_page", &[("tree", tree), ("count", files_n(c.left_out_count() - tracked.len() as i64)), ("reasons", reasons(&Capture { left_out: untracked.into_iter().cloned().collect(), ..c.clone() }))]);
                if !tracked.is_empty() {
                    let count = if tracked.len() == 1 { "1 file is".to_string() } else { format!("{} files are", tracked.len()) };
                    let why = reasons(&Capture { left_out: tracked.into_iter().cloned().collect(), left_out_more: 0, unnamed: 0, ..c.clone() });
                    line.push(' ');
                    line.push_str(&evidence("part_tracked_page", &[("count", count), ("commit", trunc(c.commit.as_deref().unwrap_or(""), 12)), ("why", why.split_once(' ').map(|x| x.1.to_string()).unwrap_or(why.clone()))]));
                }
                return vec![line];
            }
            let mut line = evidence("part", &[("tree", tree)]);
            let named = lacks(c);
            if !named.is_empty() {
                line.push(' ');
                line.push_str(&evidence("part_not_in", &[("files", named)]));
            }
            let commit = trunc(c.commit.as_deref().unwrap_or(""), 12);
            for l in c.left_out.iter().filter(|l| l.tracked) {
                line.push(' ');
                line.push_str(&evidence("part_tracked", &[("path", l.path.clone()), ("commit", commit.clone()), ("why", reason(&l.why))]));
            }
            if c.unnamed > 0 {
                line.push(' ');
                line.push_str(&if c.unnamed == 1 { evidence("part_unnamed_one", &[]) } else { evidence("part_unnamed", &[("count", format!("{} were", files_n(c.unnamed)))]) });
            }
            vec![line]
        }
    }
}

fn none_line(c: &Capture, page: bool) -> Option<String> {
    let listing = if c.listing == "changed" { "changed".to_string() } else { "untracked".to_string() };
    let secs = if c.git_secs.is_empty() { "30".to_string() } else { c.git_secs.clone() };
    let when = if c.when == When::Start { "when the run started" } else { "when it recorded the run" }.to_string();
    Some(match c.why.as_str() {
        "not_in_git" | "no_commit" => return None,
        "git_timeout" => evidence("no_code_timeout", &[("secs", secs)]),
        "git_unreadable" if page || c.git_said.is_empty() => evidence("no_code_git_page", &[("when", when)]),
        "git_unreadable" => evidence("no_code_git", &[("git_said", c.git_said.clone())]),
        "git_failed" if page || c.git_said.is_empty() => evidence("no_code_failed_bare", &[]),
        "git_failed" => evidence("no_code_failed", &[("git_said", c.git_said.clone())]),
        "list_failed" if page || c.git_said.is_empty() => evidence("no_code_list_page", &[("listing", listing)]),
        "list_failed" => evidence("no_code_list", &[("listing", listing), ("git_said", c.git_said.clone())]),
        "too_many" => match &c.too_many {
            Some(t) if !t.folder.is_empty() && page => evidence("no_code_too_many_page", &[("files", grouped(t.files)), ("in_folder", grouped(t.in_folder)), ("folder", t.folder.clone())]),
            Some(t) if !t.folder.is_empty() => evidence("no_code_too_many", &[("files", grouped(t.files)), ("in_folder", grouped(t.in_folder)), ("folder", t.folder.clone())]),
            // an earlier rb's receipt names no folder, and a page gives no advice
            Some(t) if c.earlier || page => evidence("no_code_too_many_flat_page", &[("files", grouped(t.files))]),
            Some(t) => evidence("no_code_too_many_flat", &[("files", grouped(t.files))]),
            None => evidence("no_code", &[]),
        },
        _ if !c.dirty => return None,
        _ => evidence("no_code", &[]),
    })
}

/// The line rb journal run says at a command's start where there are too many files to keep its code; None otherwise.
pub fn too_many_at_start(snap: &Dict) -> Option<String> {
    let t = snap.get("too_many")?.as_dict()?;
    let files = grouped(int(t, "files"));
    Some(match text(t, "folder") {
        Some(folder) => evidence("too_many_start", &[("files", files), ("in_folder", grouped(int(t, "in_folder"))), ("folder", folder)]),
        None => evidence("too_many_start_flat", &[("files", files)]),
    })
}

impl Capture {
    /// Why it kept no code, in a row's short words; None where it kept some.
    pub fn none_why(&self) -> Option<String> {
        if self.kept != Kept::None {
            return None;
        }
        let listing = if self.listing == "changed" { "changed" } else { "untracked" };
        Some(match self.why.as_str() {
            "git_unreadable" => evidence("why_git", &[]),
            "git_failed" => evidence("why_failed", &[]),
            "git_timeout" => evidence("why_timeout", &[("secs", if self.git_secs.is_empty() { "30".to_string() } else { self.git_secs.clone() })]),
            "list_failed" => evidence("why_list", &[("listing", listing.to_string())]),
            "too_many" => evidence("why_too_many", &[("files", grouped(self.too_many.as_ref().map_or(0, |t| t.files)))]),
            "not_in_git" | "no_commit" => return None,
            _ if !self.dirty => return None,
            _ => evidence("why_not_kept", &[]),
        })
    }

    /// Why it kept no code, in a row's short words, for a capture that kept none: none_why's words, and where git had
    /// no commit to keep it at (or the record is outside git), those words (S4), never "never committed".
    pub fn none_reason(&self) -> String {
        self.none_why().unwrap_or_else(|| evidence(if self.why == "no_commit" || self.why == "not_in_git" { "why_no_commit" } else { "why_not_kept" }, &[]))
    }

    /// What a part-kept capture lacks, in a row's short words; None where it kept the whole code or none.
    pub fn part_what(&self, page: bool) -> Option<String> {
        if self.kept != Kept::Part {
            return None;
        }
        let named = lacks(self);
        let tracked: Vec<String> = self.left_out.iter().filter(|l| l.tracked).map(|l| format!("{} as its commit holds it", l.path)).collect();
        let what = if page || (named.is_empty() && tracked.is_empty()) {
            format!("{} left out ({})", files_n(self.left_out_count()), reasons(self))
        } else {
            [named].into_iter().filter(|s| !s.is_empty()).chain(tracked).collect::<Vec<_>>().join(", ")
        };
        Some(evidence("row_part", &[("what", what)]))
    }

    /// The line Reproduce puts over a part-kept tree's rebuild: what it lacks and that those files go in place after
    /// its commands. None where it kept the whole code.
    pub fn rebuild_line(&self, page: bool) -> Option<String> {
        if self.kept != Kept::Part {
            return None;
        }
        // a file git tracks is in the tree as its commit holds it: what the tree lacks is the run's own copy of it
        let (tracked, untracked): (Vec<LeftOut>, Vec<LeftOut>) = self.left_out.iter().cloned().partition(|l| l.tracked);
        if page {
            // the card's counts: the files not in the tree, then the ones in it as their commit holds them
            let mut lacking: Vec<String> = Vec::new();
            let loose = self.left_out_count() - tracked.len() as i64;
            if loose > 0 {
                lacking.push(format!("{} ({})", files_n(loose), reasons(&Capture { left_out: untracked, ..self.clone() })));
            }
            if !tracked.is_empty() {
                let why = reasons(&Capture { left_out: tracked.clone(), left_out_more: 0, unnamed: 0, ..self.clone() });
                let it = if tracked.len() == 1 { "it" } else { "them" };
                lacking.push(format!("{} as the run had {it} ({})", files_n(tracked.len() as i64), why.split_once(' ').map(|x| x.1.to_string()).unwrap_or(why.clone())));
            }
            return Some(evidence("part_rebuild_page", &[("lacks", lacking.join(" and "))]));
        }
        let mut files: Vec<String> = untracked.iter().map(|l| format!("{} ({})", l.path, reason(&l.why))).collect();
        files.extend(tracked.iter().map(|l| format!("{} as the run had it ({})", l.path, reason(&l.why))));
        if self.left_out_more > 0 {
            files.push(evidence("part_more", &[("count", grouped(self.left_out_more))]));
        }
        if self.unnamed > 0 {
            files.push(format!("{} not named", files_n(self.unnamed)));
        }
        Some(evidence("part_rebuild", &[("files", files.join(", "))]))
    }
}

/// The folder holding the most of `paths` (each from the repository's top), with "/" after it, and how many it holds:
/// the deepest folder that still holds at least half of what the top folder holding most of them holds. None where
/// most of them sit at the top.
pub fn busiest_folder(paths: &[String]) -> Option<(String, i64)> {
    let mut prefix = String::new();
    let mut held: i64 = paths.len() as i64;
    loop {
        let mut counts: std::collections::BTreeMap<String, i64> = std::collections::BTreeMap::new();
        for p in paths.iter().filter(|p| p.starts_with(&prefix)) {
            let rest = &p[prefix.len()..];
            if let Some(i) = rest.find('/') {
                *counts.entry(format!("{prefix}{}/", &rest[..i])).or_default() += 1;
            }
        }
        let best = counts.into_iter().max_by(|a, b| a.1.cmp(&b.1).then(b.0.cmp(&a.0)));
        match best {
            Some((folder, n)) if prefix.is_empty() || n * 2 >= held => {
                if prefix.is_empty() && n * 2 < paths.len() as i64 && n < 2 {
                    return None;
                }
                prefix = folder;
                held = n;
            }
            _ => break,
        }
    }
    (!prefix.is_empty()).then_some((prefix, held))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn d(v: Value) -> Dict {
        v.as_dict().cloned().unwrap()
    }

    #[test]
    fn an_earlier_rbs_receipt_reads_as_that_rb_kept_it() {
        let clean = Capture::of(&d(crate::obj! {"commit" => "abc", "dirty" => false}), When::After);
        assert_eq!((clean.kept, clean.code()), (Kept::Whole, Some(("abc".into(), "abc".into()))));
        let part = Capture::of(&d(crate::obj! {"commit" => "abc", "dirty" => true, "tree" => "t1", "left_out" => 2}), When::After);
        assert_eq!((part.kept, part.unnamed, part.code()), (Kept::Part, 2, Some(("abc".into(), "t1".into()))));
        assert_eq!(state_lines(&part, false), vec!["rb kept part of this run's code: git tree t1. 2 files were left out of it; this record does not name them.".to_string()]);
        assert_eq!(state_lines(&part, true), vec!["rb kept part of this run's code: git tree t1. Not in it: 2 files (2 not named).".to_string()]);
        let many = Capture::of(&d(crate::obj! {"commit" => "abc", "dirty" => true, "tree" => Value::Null, "left_out" => 2005}), When::After);
        assert_eq!((many.kept, many.code()), (Kept::None, None));
        assert_eq!(state_lines(&many, false), vec!["rb kept no code for this run: 2005 files changed or untracked.".to_string()]);
        let none = Capture::of(&Dict::new(), When::After);
        assert_eq!((none.kept, none.why.as_str()), (Kept::None, "not_in_git"));
        assert!(state_lines(&none, false).is_empty());
    }

    #[test]
    fn the_words_of_each_state() {
        let failed = Capture::of(&d(crate::obj! {"commit" => "abc", "dirty" => true, "tree" => Value::Null, "kept" => "none", "kept_why" => "git_unreadable", "git_said" => "fatal: bad index"}), When::After);
        assert_eq!(state_lines(&failed, false), vec!["rb kept no code for this run: rb could not read git (fatal: bad index).".to_string()]);
        assert_eq!(state_lines(&failed, true), vec!["rb kept no code for this run: rb could not read git when it recorded the run.".to_string()]);
        let slow = Capture::of(&d(crate::obj! {"commit" => "abc", "dirty" => true, "kept" => "none", "kept_why" => "git_timeout", "git_secs" => 30}), When::After);
        assert_eq!(state_lines(&slow, false), vec!["rb kept no code for this run: git did not answer in 30 s.".to_string()]);
        let many = Capture::of(
            &d(crate::obj! {"commit" => "abc", "dirty" => true, "kept" => "none", "kept_why" => "too_many", "too_many" => crate::obj! {"files" => 2005, "folder" => "logs/", "in_folder" => 1950}}),
            When::After,
        );
        assert_eq!(
            state_lines(&many, false),
            vec!["rb kept no code for this run: 2005 files changed or untracked, 1950 of them in logs/. Add such folders to .gitignore, or commit them, so the next run's code is kept.".to_string()]
        );
        let part = Capture::of(
            &d(crate::obj! {"commit" => "0123456789abcdef", "dirty" => true, "tree" => "fedcba9876543210", "kept" => "part", "kept_why" => "left_out",
            "left_out" => 3, "left_out_files" => crate::list![crate::obj! {"path" => "embeddings.bin", "why" => "over_10_mb", "tracked" => false}, crate::obj! {"path" => "data/link", "why" => "not_regular", "tracked" => false}, crate::obj! {"path" => "data/big.bin", "why" => "over_10_mb", "tracked" => true}]}),
            When::After,
        );
        assert_eq!(
            state_lines(&part, false),
            vec!["rb kept part of this run's code: git tree fedcba987654. Not in it: embeddings.bin (over 10 MB), data/link (not a regular file). data/big.bin is in it as commit 0123456789ab holds it, not as the run had it (over 10 MB).".to_string()]
        );
        assert_eq!(state_lines(&part, true), vec!["rb kept part of this run's code: git tree fedcba987654. Not in it: 2 files (1 over 10 MB, 1 not a regular file). 1 file is in it as commit 0123456789ab holds it, not as the run had it (over 10 MB).".to_string()]);
        assert_eq!(grouped(2005), "2005");
    }

    #[test]
    fn the_busiest_folder_is_the_deepest_that_holds_most() {
        let mut paths: Vec<String> = (0..1950).map(|i| format!("logs/run/{i}.txt")).collect();
        paths.extend((0..55).map(|i| format!("f{i}.py")));
        assert_eq!(busiest_folder(&paths), Some(("logs/run/".into(), 1950)));
        let flat: Vec<String> = (0..10).map(|i| format!("f{i}.py")).collect();
        assert_eq!(busiest_folder(&flat), None);
    }
}
