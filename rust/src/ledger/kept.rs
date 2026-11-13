//! rb's kept copy of what it wrote to the record (EVIDENCE.md E1). git trusts no one's log: `git checkout -- .`,
//! `git restore`, `git reset --hard` and `git stash` put .rb/log.jsonl back to what a commit holds, and every record rb
//! wrote since would then read as written outside rb. Every line rb appends to the log, with the record forms it wrote,
//! also goes to an append-only copy in the journal's own folder (<git common dir>/rb/journal/<worktree>/kept.jsonl;
//! .rb/journal/ while git's folder cannot be written), with the commit HEAD named and its branch. A kept write is taken
//! out by git only when the log on disk lacks its line and no commit HEAD has named since holds it (HEAD's log, then its
//! reflog, newest first). Outside git nothing is kept: git cannot revert that record. Older rb never sees the copy.

use super::*;

pub const KEPT_FILE: &str = "kept.jsonl";

/// One write a kept line made: the record, its hash and file, and its form as rb wrote it.
#[derive(Clone, Debug)]
pub struct KeptWrite {
    pub key: Key,
    pub sha: String,
    pub path: String,
    pub form: Option<Dict>,
}

/// One kept log line: its text exactly as rb appended it, when it was written, and what it wrote.
#[derive(Clone, Debug)]
pub struct KeptLine {
    pub line: String,
    pub at: String,
    pub writes: Vec<KeptWrite>,
}

/// What git took out of the record: the kept lines the log lacks that no commit HEAD named since holds, in the order
/// rb wrote them, and the stash that holds them when one does.
#[derive(Clone, Debug, Default)]
pub struct TakenOut {
    pub lines: Vec<KeptLine>,
    /// the records those lines wrote, each once, in order
    pub records: Vec<Key>,
    /// the stash entry that holds every one of the lines (stash@{n}), and whether the log is as that stash left it, so
    /// git stash pop puts them back
    pub stash: Option<(String, bool)>,
}

impl TakenOut {
    pub fn is_empty(&self) -> bool {
        self.records.is_empty()
    }

    /// Whether git stash pop is what puts them back (rb offers no restore then).
    pub fn pop(&self) -> Option<&str> {
        self.stash.as_ref().filter(|(_, pop)| *pop).map(|(s, _)| s.as_str())
    }

    /// The hashes each record's taken-out writes had.
    pub fn shas(&self) -> HashMap<Key, HashSet<String>> {
        let mut out: HashMap<Key, HashSet<String>> = HashMap::new();
        for l in &self.lines {
            for w in &l.writes {
                out.entry(w.key.clone()).or_default().insert(w.sha.clone());
            }
        }
        out
    }
}

fn line_hash(line: &str) -> String {
    sha256_bytes(line.as_bytes())
}

/// The kept copy's files: where it is written now, then where an earlier look kept it.
fn kept_paths(root: &Path) -> Vec<PathBuf> {
    let mut out = vec![crate::journal::read_dir(root).join(KEPT_FILE)];
    if let Some(a) = crate::journal::aside_dir(root) {
        let p = a.join(KEPT_FILE);
        if !out.contains(&p) {
            out.push(p);
        }
    }
    out
}

/// The last line of a file, read from its end.
fn last_line(path: &Path) -> Option<String> {
    use std::io::{Read, Seek, SeekFrom};
    let mut f = std::fs::File::open(path).ok()?;
    let len = f.metadata().ok()?.len();
    let mut take: u64 = 4096;
    loop {
        let start = len.saturating_sub(take);
        f.seek(SeekFrom::Start(start)).ok()?;
        let mut buf = Vec::new();
        f.read_to_end(&mut buf).ok()?;
        let text = String::from_utf8_lossy(&buf);
        let body = text.trim_end_matches(['\n', '\r']);
        if let Some(i) = body.rfind('\n') {
            return Some(body[i + 1..].to_string());
        }
        if start == 0 {
            return (!body.is_empty()).then(|| body.to_string());
        }
        take *= 4;
    }
}

/// The experiment a log line confirms the design of (rb confirm <exp>@<hash>: op gate, a log line alone that writes no
/// file), which git takes out as it takes out any write of rb's (N3, f13i).
pub(crate) fn design_confirmed(line: &str) -> Option<Key> {
    let Ok(Value::Dict(d)) = pyjson::loads(line) else { return None };
    if d.get_str("op") != Some("gate") || d.get_str("sha256").is_some() || d.get("parts").map_or(false, |p| p.truthy()) {
        return None;
    }
    let detail = d.get("detail")?;
    if detail.get("owned").is_none() && detail.get("confirms").is_none() {
        return None;
    }
    Some((d.get_str("kind").unwrap_or("experiment").to_string(), d.get_str("id")?.to_string()))
}

/// Whether a line git took out counts as a write taken out: one that wrote a record, or a design's confirmation.
fn counts(l: &KeptLine) -> bool {
    !l.writes.is_empty() || design_confirmed(&l.line).is_some()
}

impl Ledger {
    /// The experiments whose design's confirmation git took out of the record (N3): rb confirm lists none of them to
    /// confirm again while the take-out stands, and refuses to confirm one, since rb doctor --restore puts it back.
    pub fn taken_design_confirms(&self) -> Res<HashSet<String>> {
        Ok(self.taken_out()?.lines.iter().filter_map(|l| design_confirmed(&l.line)).map(|k| k.1).collect())
    }

    /// What a design's views say while git took its confirmation out of the record (N3): "its confirmation was taken
    /// out of the record by git: rb reads it again once it is put back", or its stash form.
    pub fn took_confirm_said(&self, _exp: &str) -> Res<String> {
        Ok(match self.taken_out()?.pop() {
            Some(stash) => crate::shell::strings::evidence("took_call_pop", &[("part", "its design's confirmation".to_string()), ("stash", stash.to_string())]),
            None => crate::shell::strings::evidence("took_call", &[("part", "its design's confirmation".to_string())]),
        })
    }

    /// rb confirm's refusal of what git took the confirmation of out of the record (N3, MP-1111-0616's L-a): the
    /// confirmation rb wrote comes back with the rest, so a second one would make two.
    pub fn taken_confirm_refusal(&self, what: &str) -> RBError {
        let (back, how) = match self.taken_out().ok().and_then(|t| t.pop().map(str::to_string)) {
            Some(stash) => (format!("git stash pop {stash}"), "is in git's stash"),
            None => ("rb doctor --restore".to_string(), "was taken out of the record by git"),
        };
        RBError::msg_fix("E_OBJECT_INVALID", format!("{what} was confirmed, and that confirmation {how}: confirming it again would record it twice once it is put back."), format!("{back} puts it back."))
    }

    /// Whether this record is one git can revert: in a repository, so a kept copy is kept.
    fn keeps_copy(&self) -> bool {
        crate::journal::aside_dir(&self.root).is_some()
    }

    /// Append the line rb has just appended to the log, with the forms it wrote and HEAD's commit and branch, to the
    /// kept copy. Never in a preview, and never outside git.
    pub(super) fn keep_line(&self, line: &str, entry: &Dict, took: bool) {
        if crate::dry::active() || !self.keeps_copy() {
            return;
        }
        let mut writes: Vec<Value> = Vec::new();
        for ((kind, id), w) in writes_in(entry) {
            let (Some(sha), Some(path)) = (w.get_str("sha256"), w.get_str("path")) else { continue };
            let form = self
                .in_(&kind, &self.root.join(path))
                .ok()
                .and_then(|items| items.iter().find(|(i, _)| *i == id || kind == "project").map(|(_, f)| f.clone()))
                .filter(|f| rbyaml::content_sha(&Value::Dict(f.clone())).ok().as_deref() == Some(sha));
            writes.push(crate::obj! {"kind" => kind, "id" => id, "sha256" => sha, "path" => path, "form" => form.map(Value::Dict).unwrap_or(Value::Null)});
        }
        let head = sources::git(&self.root, &["rev-parse", "--verify", "-q", "HEAD"]).map(|s| s.trim().to_string()).filter(|s| !s.is_empty());
        let branch = sources::git(&self.root, &["symbolic-ref", "-q", "--short", "HEAD"]).map(|s| s.trim().to_string()).filter(|s| !s.is_empty());
        let size = crate::dry::size_time(&self.dir.join("log.jsonl")).map(|(s, _)| s as i64).unwrap_or(0);
        let kept = crate::obj! {
            "line" => line.trim_end_matches('\n'), "at" => entry.get_str("at").unwrap_or(""), "head" => Value::opt_str(head),
            "branch" => Value::opt_str(branch), "size" => size, "writes" => Value::List(writes)
        };
        let mut kept = kept;
        if took {
            if let Value::Dict(d) = &mut kept {
                d.insert("after_taken".into(), Value::Bool(true));
            }
        }
        let dir = crate::journal::journal_dir(&self.root);
        let _ = crate::dry::append(&dir.join(KEPT_FILE), pyjson::jsonl_line(&kept).as_bytes());
        *self.git_took.borrow_mut() = None;
    }

    /// What git took out of the record, worked out once for each state of the log.
    pub fn taken_out(&self) -> Res<Rc<TakenOut>> {
        let logp = self.dir.join("log.jsonl");
        let stamp = crate::dry::size_time(&logp);
        if let Some((s, t)) = self.git_took.borrow().as_ref() {
            if *s == stamp {
                return Ok(t.clone());
            }
        }
        let t = Rc::new(self.taken_out_now(stamp.map(|(s, _)| s).unwrap_or(0)).unwrap_or_default());
        *self.git_took.borrow_mut() = Some((stamp, t.clone()));
        Ok(t)
    }

    fn taken_out_now(&self, log_size: u64) -> Option<TakenOut> {
        if !self.keeps_copy() {
            return None;
        }
        let paths: Vec<PathBuf> = kept_paths(&self.root).into_iter().filter(|p| p.is_file()).collect();
        if paths.is_empty() {
            return None;
        }
        let logp = self.dir.join("log.jsonl");
        // the cheap path: the log is as rb last left it (its size, and its last line rb's last kept line)
        if paths.len() == 1 {
            if let Some(Ok(Value::Dict(last))) = last_line(&paths[0]).map(|l| pyjson::loads(&l)) {
                if last.get("after_taken").is_none() && last.get("size").and_then(|v| v.as_i64()) == Some(log_size as i64) && last.get_str("line").is_some() && last_line(&logp).as_deref() == last.get_str("line") {
                    return None;
                }
            }
        }
        let log_text = crate::dry::read(&logp).map(|b| String::from_utf8_lossy(&b).into_owned()).unwrap_or_default();
        let on_disk: HashSet<&str> = py_splitlines(&log_text).into_iter().collect();
        let mut forgot: HashSet<String> = HashSet::new();
        let mut kept: Vec<KeptLine> = Vec::new();
        let mut seen: HashSet<String> = HashSet::new();
        for p in &paths {
            let text = std::fs::read(p).map(|b| String::from_utf8_lossy(&b).into_owned()).unwrap_or_default();
            for raw in text.split('\n') {
                let Ok(Value::Dict(d)) = pyjson::loads(raw.trim()) else { continue };
                if let Some(Value::List(f)) = d.get("forget") {
                    forgot.extend(f.iter().filter_map(|h| h.as_str().map(str::to_string)));
                    continue;
                }
                let Some(line) = d.get_str("line") else { continue };
                if on_disk.contains(line) || !seen.insert(line_hash(line)) {
                    continue;
                }
                let writes: Vec<KeptWrite> = match d.get("writes") {
                    Some(Value::List(ws)) => ws
                        .iter()
                        .filter_map(|w| {
                            Some(KeptWrite {
                                key: (w.get_str("kind")?.to_string(), w.get_str("id")?.to_string()),
                                sha: w.get_str("sha256")?.to_string(),
                                path: w.get_str("path")?.to_string(),
                                form: w.get("form").and_then(|f| f.as_dict()).cloned(),
                            })
                        })
                        .collect(),
                    _ => Vec::new(),
                };
                kept.push(KeptLine { line: line.to_string(), at: d.get_str("at").unwrap_or("").to_string(), writes });
            }
        }
        kept.retain(|l| !forgot.contains(&line_hash(&l.line)));
        // a confirmation is a log line alone: one the log holds by its time, op and record, rewritten in place (an
        // older rb's form of it, a hand), is not taken out; the log's own check says what changed
        let stamp = |d: &Dict| ["at", "op", "kind", "id"].map(|k| d.get_str(k).unwrap_or("").to_string());
        let stamps: HashSet<[String; 4]> = on_disk.iter().filter_map(|l| match pyjson::loads(l) { Ok(Value::Dict(d)) => Some(stamp(&d)), _ => None }).collect();
        kept.retain(|l| {
            !(l.writes.is_empty() && design_confirmed(&l.line).is_some() && matches!(pyjson::loads(&l.line), Ok(Value::Dict(d)) if stamps.contains(&stamp(&d))))
        });
        if !kept.iter().any(counts) {
            return None;
        }
        // a line a commit HEAD has named since rb wrote it holds is in git, not taken out
        let (_, _, prefix) = crate::journal::git_dirs(&self.root)?;
        let rel = self.rel(&logp);
        let in_repo = if prefix.is_empty() { rel.clone() } else { format!("{prefix}/{rel}") };
        let since = kept.iter().filter_map(|l| crate::pytime::parse_iso(&l.at)).map(|t| t.timestamp()).min().unwrap_or(0) - 600;
        let mut commits: Vec<String> = vec!["HEAD".into()];
        if let Ok(r) = sources::git_full(&self.root, &["reflog", "--date=unix", "--format=%H %gd", "HEAD"], None) {
            for row in String::from_utf8_lossy(&r.stdout).lines() {
                let Some((sha, gd)) = row.split_once(' ') else { continue };
                let when = gd.rsplit_once('{').and_then(|(_, t)| t.trim_end_matches('}').parse::<i64>().ok()).unwrap_or(i64::MAX);
                if when < since {
                    break;
                }
                if !commits.iter().any(|c| c == sha) {
                    commits.push(sha.to_string());
                }
            }
        }
        let batch: String = commits.iter().map(|c| format!("{c}:{in_repo}\n")).collect();
        let blobs = cat_blobs(&self.root, &batch);
        // git can take out only what it holds: a log no commit HEAD names holds was never git's to put back
        if !matches!(blobs.first(), Some(Some(_))) {
            return None;
        }
        for blob in blobs.into_iter().flatten() {
            let held: HashSet<&str> = py_splitlines(&blob).into_iter().collect();
            kept.retain(|l| !held.contains(l.line.as_str()));
            if kept.is_empty() {
                break;
            }
        }
        if !kept.iter().any(counts) {
            return None;
        }
        // a stash that holds them: git stash pop puts them back while the log is as that stash left it
        let mut stash = None;
        if let Ok(r) = sources::git_full(&self.root, &["stash", "list", "--format=%gd"], None) {
            for name in String::from_utf8_lossy(&r.stdout).lines().map(str::trim).filter(|n| !n.is_empty()) {
                let blobs = cat_blobs(&self.root, &format!("{name}:{in_repo}\n{name}^1:{in_repo}\n"));
                let Some(Some(stashed)) = blobs.first() else { continue };
                let held: HashSet<&str> = py_splitlines(stashed).into_iter().collect();
                if kept.iter().filter(|l| counts(l)).all(|l| held.contains(l.line.as_str())) {
                    let pop = matches!(blobs.get(1), Some(Some(base)) if *base == log_text);
                    stash = Some((name.to_string(), pop));
                    break;
                }
            }
        }
        let mut records: Vec<Key> = Vec::new();
        for l in &kept {
            for k in l.writes.iter().map(|w| w.key.clone()).chain(design_confirmed(&l.line)) {
                if !records.contains(&k) {
                    records.push(k);
                }
            }
        }
        Some(TakenOut { lines: kept, records, stash })
    }

    /// Whether git took this record's write out of the record (E1): rb show says so.
    pub fn taken_record(&self, kind: Option<&str>, id: &str) -> Res<Option<Key>> {
        let t = self.taken_out()?;
        Ok(t.records.iter().find(|(k, i)| i == id && kind.map_or(true, |kk| kk == k)).cloned())
    }

    /// The records git took a write of out of the record that sit under a record, and leave its lines (L1): a note, a
    /// run, an analysis, a variant or a criterion of an experiment; a sub-question, an experiment filed under a question
    /// or a criterion linked to it; a relation another criterion declared on a criterion; a call on any of them. rb show
    /// <id> and its card on rb ui name them, since a listing read whole cannot say which of them it would have shown.
    pub fn taken_under(&self, kind: &str, id: &str) -> Res<Vec<Key>> {
        let t = self.taken_out()?;
        if t.is_empty() {
            return Ok(Vec::new());
        }
        let has = |f: Option<&Dict>, k: &str, pred: &dyn Fn(&Value) -> bool| matches!(f.and_then(|f| f.get(k)), Some(Value::List(l)) if l.iter().any(|x| pred(x)));
        let prefix = format!("{id}/");
        let mut out: Vec<Key> = Vec::new();
        for w in t.lines.iter().flat_map(|l| l.writes.iter()) {
            if out.contains(&w.key) || (w.key.0 == kind && w.key.1 == id) {
                continue;
            }
            let f = w.form.as_ref();
            let subject = f.and_then(|f| f.get_str("subject")).unwrap_or("");
            let of_exp = Ledger::path_experiment(&w.key.0, Path::new(&w.path.replace('\\', "/")));
            let under = subject == id
                || match kind {
                    // a call on one of its criteria is under it too (f13i)
                    "experiment" => {
                        of_exp.as_deref() == Some(id)
                            || subject.starts_with(&prefix)
                            || (!subject.is_empty() && self.load::<Criterion>(subject).map_or(false, |c| c.experiment == id))
                    }
                    "question" => {
                        f.and_then(|f| f.get_str("parent")) == Some(id)
                            || has(f, "questions", &|x| x.as_str() == Some(id))
                            || has(f, "question_links", &|x| x.get_str("question") == Some(id))
                    }
                    "criterion" => has(f, "relations", &|x| x.get_str("target") == Some(id)),
                    _ => false,
                };
            if under {
                out.push(w.key.clone());
            }
        }
        Ok(out)
    }

    /// What rb show <id> and its card say of the records under it git took out (taken_under), or None.
    pub fn took_under_said(&self, kind: &str, id: &str) -> Res<Option<String>> {
        let under = self.taken_under(kind, id)?;
        if under.is_empty() {
            return Ok(None);
        }
        let idx = self.where_()?;
        let one = under.iter().all(|k| k.0 == under[0].0);
        let plural = |k: &str| if k == "analysis" { "analyses".to_string() } else { format!("{k}s") };
        let on_disk = under.iter().all(|k| idx.contains_key(k));
        let what = match (one, under.len(), on_disk) {
            (true, 1, true) => format!("1 write to {} {}", if under[0].0.starts_with(['a', 'e', 'i', 'o', 'u']) { "an" } else { "a" }, under[0].0),
            (true, n, true) => format!("writes to {n} {}", plural(&under[0].0)),
            (true, 1, false) => format!("1 {}", under[0].0),
            (true, n, false) => format!("{n} {}", plural(&under[0].0)),
            (false, n, _) => format!("{n} records"),
        };
        let [_, were, are, them] = crate::shell::strings::took_fill(under.len());
        let ids = crate::assess::first_few(&under.iter().map(|k| k.1.clone()).collect::<Vec<_>>(), 6, ", ");
        Ok(Some(match self.taken_out()?.pop() {
            Some(stash) => crate::shell::strings::evidence("took_under_pop", &[("what", what), ("are", are), ("ids", ids), ("stash", stash.to_string()), ("them", them)]),
            None => crate::shell::strings::evidence("took_under", &[("what", what), ("were", were), ("ids", ids), ("them", them)]),
        }))
    }

    /// The writes git took out of the record that a line about `subject` may rest on (f13i): its own, the records
    /// under it (taken_under), and, for a record of an experiment (the experiment, a criterion, a run, an analysis, a
    /// setting as <exp>/<name>), everything taken out of that experiment, the resources its runs read and the
    /// catalogue entries its criteria name; for a question, its experiments'; for a folder, the experiment indexed on
    /// it. A line that cannot say which of them it read fails closed on all of them, as taken_for falls back.
    pub fn took_scope(&self, subject: &str) -> Res<Vec<Key>> {
        let t = self.taken_out()?;
        if t.is_empty() || subject.is_empty() {
            return Ok(Vec::new());
        }
        let mut out: Vec<Key> = Vec::new();
        let add = |out: &mut Vec<Key>, ks: Vec<Key>| {
            for k in ks {
                if !out.contains(&k) {
                    out.push(k);
                }
            }
        };
        let (head, name) = super::parse_address(subject);
        let kind = if name.is_some() { None } else { self.kind_of(subject)? };
        if let Some(k) = self.taken_record(None, subject)? {
            add(&mut out, vec![k]);
        }
        if let Some(kind) = kind {
            add(&mut out, self.taken_under(kind, subject)?);
        }
        // writes to the setting's or the record's own address: a call on <exp>/<name>
        add(&mut out, self.taken_under("setting", subject)?);
        let mut exps: Vec<String> = Vec::new();
        match (kind, &name) {
            (_, Some(_)) => exps.push(head.clone()),
            (Some("experiment"), _) => exps.push(subject.to_string()),
            (Some("criterion"), _) => exps.extend(self.load::<Criterion>(subject).ok().map(|c| c.experiment)),
            (Some("run"), _) => exps.extend(self.load::<Run>(subject).ok().map(|r| r.experiment)),
            (Some("analysis"), _) => exps.extend(self.load::<Analysis>(subject).ok().map(|a| a.experiment)),
            (Some("question"), _) => {
                exps.extend(self.all::<Experiment>()?.iter().filter(|e| e.questions.iter().any(|q| q == subject)).map(|e| e.id.clone()));
                // an experiment filed under it that git took out, as rb wrote it
                for w in t.lines.iter().flat_map(|l| l.writes.iter()).filter(|w| w.key.0 == "experiment") {
                    if matches!(w.form.as_ref().and_then(|f| f.get("questions")), Some(Value::List(l)) if l.iter().any(|x| x.as_str() == Some(subject))) {
                        exps.push(w.key.1.clone());
                    }
                }
            }
            (Some("resource"), _) => {
                // a run that read it, git took a write of out of the record (its retraction, the run itself)
                for w in t.lines.iter().flat_map(|l| l.writes.iter()).filter(|w| w.key.0 == "run") {
                    let reads = |f: &Dict| matches!(f.get("inputs"), Some(Value::List(l)) if l.iter().any(|u| u.get_str("resource") == Some(subject)));
                    let on_disk = self.load::<Run>(&w.key.1).ok().map_or(false, |r| r.inputs.iter().any(|u| u.resource == subject));
                    if on_disk || w.form.as_ref().map_or(false, reads) {
                        add(&mut out, vec![w.key.clone()]);
                    }
                }
            }
            _ => {}
        }
        // a results folder: the experiment indexed on it, as rb wrote it
        for w in t.lines.iter().flat_map(|l| l.writes.iter()).filter(|w| w.key.0 == "experiment") {
            if w.form.as_ref().and_then(|f| f.get_str("folder")).map(|f| f.trim_end_matches('/')) == Some(subject.trim_end_matches('/')) {
                add(&mut out, vec![w.key.clone()]);
            }
        }
        for exp in exps {
            if let Some(k) = self.taken_record(Some("experiment"), &exp)? {
                add(&mut out, vec![k]);
            }
            add(&mut out, self.taken_under("experiment", &exp)?);
            let runs: Vec<Run> = self.all::<Run>()?.iter().filter(|r| r.experiment == exp).cloned().collect();
            let read: HashSet<String> = runs.iter().flat_map(|r| r.inputs.iter().map(|u| u.resource.clone())).collect();
            let names: HashSet<String> = self
                .all::<Criterion>()?
                .iter()
                .filter(|c| c.experiment == exp)
                .flat_map(|c| {
                    let m = c.metric.clone();
                    let base = m.rsplit('.').next().unwrap_or("").to_string();
                    [m, base]
                })
                .collect();
            for w in t.lines.iter().flat_map(|l| l.writes.iter()) {
                let named = match w.key.0.as_str() {
                    "resource" => read.contains(&w.key.1),
                    "metric" => {
                        names.contains(&w.key.1)
                            || matches!(w.form.as_ref().and_then(|f| f.get("aliases")), Some(Value::List(l)) if l.iter().any(|a| a.as_str().map_or(false, |a| names.contains(a))))
                    }
                    _ => false,
                };
                if named {
                    add(&mut out, vec![w.key.clone()]);
                }
            }
        }
        // a note changes no reading (taken_for counts none either)
        out.retain(|k| k.0 != "note");
        // a setting rests on its experiment's file and the calls on it, never on a call on another setting
        // (MP-1111-0952's F4: a reject on int8/cuda taken out said nothing of int8/lr)
        if name.is_some() {
            let others: Vec<Key> = t
                .lines
                .iter()
                .flat_map(|l| l.writes.iter())
                .filter(|w| w.key.0 == "decision")
                .filter(|w| w.form.as_ref().and_then(|f| f.get_str("subject")).map_or(false, |s| s.starts_with(&format!("{head}/")) && s != subject))
                .map(|w| w.key.clone())
                .collect();
            out.retain(|k| !others.contains(k));
        }
        Ok(out)
    }

    /// What a line about `subject` says of the writes it may rest on that git took out (took_scope), or None: "a write
    /// it may rest on was taken out of the record by git (a write to d1): rb reads it again once it is put back". With
    /// `own` false, the subject's own write is left to the line that says it (rb show's took_said).
    pub fn took_scope_said(&self, subject: &str, own: bool) -> Res<Option<String>> {
        let mut keys = self.took_scope(subject)?;
        if !own {
            keys.retain(|k| k.1 != subject);
        }
        if keys.is_empty() {
            return Ok(None);
        }
        Ok(Some(self.took_part(if keys.len() == 1 { "a write it may rest on" } else { "what it may rest on" }, &keys)?))
    }

    /// Whether git took out the subject's own write, or a call on it: what asks for that write back in place of the
    /// subject's own command (a vouch on a setting whose reject was taken out, N2).
    pub fn took_direct(&self, subject: &str) -> Res<bool> {
        // a design's confirmation alone, a log line that wrote no file, is no write a command of the subject's rests
        // on: rb doctor --restore puts it back and leaves the item as it is (MP-1111-0952's F4)
        let t = self.taken_out()?;
        let own = match self.taken_record(None, subject)? {
            Some((kind, id)) => t.lines.iter().flat_map(|l| l.writes.iter()).any(|w| w.key.0 == kind && w.key.1 == id),
            None => false,
        };
        Ok(own || self.taken_under("setting", subject)?.iter().any(|k| k.0 == "decision"))
    }

    /// Whether git took out a write that changed whether a record is retracted (L1): its state on disk is then not the
    /// state rb last wrote (an experiment or a question retracted, and the retraction taken out).
    pub fn took_retraction(&self, kind: &str, id: &str) -> Res<bool> {
        let t = self.taken_out()?;
        let Some(w) = t.lines.iter().rev().flat_map(|l| l.writes.iter()).find(|w| w.key.0 == kind && w.key.1 == id) else { return Ok(false) };
        let kept = w.form.as_ref().and_then(|f| f.get("retracted")).map_or(false, |r| !r.is_null());
        let here = match kind {
            "experiment" => self.load::<Experiment>(id).ok().map(|e| e.retracted.is_some()),
            "question" => self.load::<Question>(id).ok().map(|q| q.retracted.is_some()),
            _ => None,
        };
        Ok(here.map_or(kept, |h| h != kept))
    }

    /// What rb show <id> opens with for a record git took a write of out of the record (E1, L1): "A write to question q1
    /// was taken out of the record by git. ...", or "run r1 was taken out ..." for one no longer on disk; None otherwise.
    pub fn took_said(&self, kind: &str, id: &str) -> Res<Option<String>> {
        let Some((kind, id)) = self.taken_record(Some(kind), id)? else { return Ok(None) };
        // a design's confirmation alone, a log line that wrote no file (N3): said as that, never as a write to its file
        let t = self.taken_out()?;
        if !t.lines.iter().flat_map(|l| l.writes.iter()).any(|w| w.key.0 == kind && w.key.1 == id) {
            return Ok(Some(format!("{}.", crate::shell::strings::evidence("took_call", &[("part", format!("The confirmation of {kind} {id}'s design"))]))));
        }
        let here = self.where_()?.contains_key(&(kind.clone(), id.clone()));
        Ok(Some(crate::shell::strings::evidence(if here { "took_show_write" } else { "took_show" }, &[("kind", kind), ("id", id)])))
    }

    /// Note a read on the reading under way (Reads), where one is traced: what the reading asked the record for.
    pub(crate) fn note_read(&self, f: impl FnOnce(&mut Reads)) {
        if let Ok(mut st) = self.reads.try_borrow_mut() {
            if let Some(top) = st.last_mut() {
                f(top);
            }
        }
    }

    /// A read the record answers by a rule of its own (a metric by its name or an alias, a resource's exposures, the
    /// calls on a subject): the rule is noted, and the records it looks through are not noted one by one.
    pub(crate) fn read_as<T>(&self, note: impl FnOnce(&mut Reads), f: impl FnOnce() -> Res<T>) -> Res<T> {
        let traced = self.reads.try_borrow().map_or(false, |st| !st.is_empty());
        if !traced {
            return f();
        }
        self.note_read(|r| {
            note(r);
            r.quiet += 1;
        });
        let out = f();
        self.note_read(|r| r.quiet -= 1);
        out
    }

    /// Run a reading, noting what it reads (only where git took something out: otherwise nothing is noted).
    pub(crate) fn traced<T>(&self, f: impl FnOnce() -> Res<T>) -> (Res<T>, Reads) {
        self.reads.borrow_mut().push(Reads::default());
        let out = f();
        let reads = self.reads.borrow_mut().pop().unwrap_or_default();
        (out, reads)
    }

    /// The records git took out that a reading rests on (B1, N1, MP-1110-2303's B1 and B2): every taken-out write the
    /// reading would have read, as its Reads say it asked the record, matched against the record rb wrote (its kept
    /// form). The reading and this list cannot drift: a read the record answers is noted where it is answered (load,
    /// all, kind_of, parts_of, catalogue_entry, metric_aliases, exposures_of, decisions_on, decisions_outside_of).
    /// Where a read is of every record of a kind (all), the rule it filters by is not known here, so every taken-out
    /// record of that kind in the experiment `exp` counts, and, for a kind no experiment holds, every one: fail closed.
    /// A note changes no reading and is never counted.
    pub fn taken_for(&self, exp: &str, reads: &Reads) -> Res<Vec<Key>> {
        // its own reads are no reading's: never noted on a reading under way around it
        self.traced(|| self.taken_for_now(exp, reads)).0
    }

    fn taken_for_now(&self, exp: &str, reads: &Reads) -> Res<Vec<Key>> {
        let t = self.taken_out()?;
        if t.is_empty() {
            return Ok(Vec::new());
        }
        // the names a run of the experiment reports, which the catalogue's aliases map (metric_aliases)
        let mut reported: HashSet<String> = reads.metrics.clone();
        if reads.aliases {
            for r in self.all::<Run>()?.iter().filter(|r| r.experiment == exp) {
                for k in r.metrics.keys() {
                    reported.insert(k.clone());
                    if let Some((_, rest)) = k.split_once('.') {
                        reported.insert(rest.to_string());
                    }
                }
            }
        }
        // the analyses a superseding one would replace in this reading: this experiment's, and those it read by id
        let supersedable: HashSet<String> = if reads.superseders {
            let mut ids: HashSet<String> = self.all::<Analysis>()?.iter().filter(|a| a.experiment == exp).map(|a| a.id.clone()).collect();
            ids.extend(reads.keys.iter().filter(|k| k.0 == "analysis").map(|k| k.1.clone()));
            ids
        } else {
            HashSet::new()
        };
        let supersedes = |f: &Dict| -> bool {
            matches!(f.get("supersedes"), Some(Value::List(l)) if l.iter().any(|x| x.as_str().map_or(false, |x| supersedable.contains(x))))
        };
        let live_metrics: HashMap<String, Metric> = self.all::<Metric>()?.iter().map(|m| (m.id.clone(), m.clone())).collect();
        let names_of = |w: &KeptWrite| -> Vec<String> {
            let mut out = vec![w.key.1.clone()];
            if let Some(Value::List(l)) = w.form.as_ref().and_then(|f| f.get("aliases")) {
                out.extend(l.iter().filter_map(|a| a.as_str().map(str::to_string)));
            }
            if let Some(m) = live_metrics.get(&w.key.1) {
                out.extend(m.aliases.iter().cloned());
            }
            out
        };
        let uses = |f: &Dict| -> Vec<(String, String)> {
            match f.get("inputs") {
                Some(Value::List(l)) => l
                    .iter()
                    .filter_map(|u| u.as_dict())
                    .filter_map(|u| u.get_str("resource").map(|r| (r.to_string(), u.get_str("role").unwrap_or("").to_string())))
                    .collect(),
                _ => Vec::new(),
            }
        };
        let mut out: Vec<Key> = Vec::new();
        for w in t.lines.iter().flat_map(|l| l.writes.iter()) {
            let (kind, id) = (&w.key.0, &w.key.1);
            if out.contains(&w.key) || kind == "note" {
                continue;
            }
            let path = w.path.replace('\\', "/");
            let of_exp = Ledger::path_experiment(kind, Path::new(&path));
            // a call's subject as rb wrote it, or, where the kept line holds no form of it, as its file says it (L1)
            let on_disk = if kind == "decision" && w.form.is_none() { self.load::<Decision>(id).ok().map(|d| d.subject) } else { None };
            let subject = w.form.as_ref().and_then(|f| f.get_str("subject")).or(on_disk.as_deref()).unwrap_or("");
            let rests = reads.keys.contains(&w.key)
                || reads.ids.contains(id)
                || reads.parts.iter().any(|(k, owner)| k == kind && owner.as_deref() == of_exp.as_deref())
                || (kind == "metric" && names_of(w).iter().any(|n| reported.contains(n)))
                || (kind == "resource" && reads.eval_on.contains(id))
                || (kind == "analysis" && w.form.as_ref().map_or(false, |f| supersedes(f)))
                || (kind == "run" && w.form.as_ref().map_or(false, |f| uses(f).iter().any(|(r, role)| reads.eval_on.contains(r) && crate::assess::is_eval_role(role))))
                || (kind == "decision" && (reads.subjects.contains(subject) || reads.prefixes.iter().any(|p| subject.starts_with(p.as_str()))))
                || (reads.kinds.contains(kind.as_str()) && (of_exp.as_deref() == Some(exp) || (!is_part(kind) && !matches!(kind.as_str(), "criterion" | "run" | "analysis"))));
            if rests {
                out.push(w.key.clone());
            }
        }
        Ok(out)
    }

    /// A line drawn from the record outside a finding's reading (L1: an experiment's or a question's state, a gate's
    /// settlement, an item made on a subject's last call), run traced, with the calls it rests on that git took a write
    /// of out of the record: what the line asked the record (decisions_on notes its subject) is matched as taken_for
    /// matches a finding's reads, so the line and what it rests on cannot drift. Nothing is traced while git took
    /// nothing out.
    pub fn traced_calls<T>(&self, f: impl FnOnce() -> Res<T>) -> Res<(T, Vec<Key>)> {
        if self.taken_out()?.is_empty() {
            return Ok((f()?, Vec::new()));
        }
        let (out, reads) = self.traced(f);
        let out = out?;
        let took: Vec<Key> = self.taken_for("", &reads)?.into_iter().filter(|k| k.0 == "decision").collect();
        Ok((out, took))
    }

    /// What a line that rests on calls git took a write of out of the record says of them (L1), as a rule line says
    /// what its evidence lost: "the call that settled it was taken out of the record by git (d1): rb reads it again once
    /// it is put back", or, in a stash, its stash form; a call still on disk is named by the write ("a write to d1").
    pub fn took_part(&self, part: &str, took: &[Key]) -> Res<String> {
        let idx = self.where_()?;
        let ids: Vec<String> = took.iter().map(|k| if idx.contains_key(k) { format!("a write to {}", k.1) } else { k.1.clone() }).collect();
        let ids = crate::assess::first_few(&ids, 6, ", ");
        Ok(match self.taken_out()?.pop() {
            Some(stash) => crate::shell::strings::evidence("took_rule_pop", &[("part", part.to_string()), ("ids", ids), ("stash", stash.to_string())]),
            None => crate::shell::strings::evidence("took_rule", &[("part", part.to_string()), ("ids", ids)]),
        })
    }

    /// The words a state takes for what git took out: "taken out by git", or "in git's stash" where git stash pop puts
    /// it back (the f13h review's later), as the lines beside it say it.
    fn took_state(&self, what: String) -> String {
        if self.taken_out().ok().map_or(false, |t| t.pop().is_some()) {
            crate::shell::strings::evidence("took_state_pop", &[("what", what)])
        } else {
            crate::shell::strings::evidence("took_state", &[("what", what)])
        }
    }

    /// The calls whose confirmation (rb confirm's log line, detail.confirms) git took out of the record.
    pub fn taken_confirms(&self) -> Res<HashSet<String>> {
        let t = self.taken_out()?;
        let mut out = HashSet::new();
        for l in &t.lines {
            let Ok(Value::Dict(d)) = pyjson::loads(&l.line) else { continue };
            match d.get("detail").and_then(|x| x.get("confirms")) {
                Some(Value::Str(id)) => {
                    out.insert(id.clone());
                }
                Some(Value::List(ids)) => out.extend(ids.iter().filter_map(|i| i.as_str().map(str::to_string))),
                _ => {}
            }
        }
        Ok(out)
    }

    /// What a listing of calls says beside a call git took a write of out of the record (L1): "a write to it was taken
    /// out of the record by git: rb reads it again once it is put back", or its stash form; None for any other call.
    pub fn took_call_mark(&self, id: &str) -> Res<Option<String>> {
        self.took_mark("decision", id)
    }

    /// The same mark beside any record a listing names (a run that read a resource, a call).
    pub fn took_mark(&self, kind: &str, id: &str) -> Res<Option<String>> {
        if self.taken_record(Some(kind), id)?.is_none() {
            return Ok(None);
        }
        let idx = self.where_()?;
        let part = if idx.contains_key(&(kind.to_string(), id.to_string())) { "a write to it" } else { "it" };
        Ok(Some(match self.taken_out()?.pop() {
            Some(stash) => crate::shell::strings::evidence("took_call_pop", &[("part", part.to_string()), ("stash", stash.to_string())]),
            None => crate::shell::strings::evidence("took_call", &[("part", part.to_string())]),
        }))
    }

    /// A state that rests on a call git took a write of out of the record, in words (L1): "a call on it taken out by
    /// git", or "a write to a call on it taken out by git" where the call is still on disk.
    pub fn took_calls_state(&self, took: &[Key]) -> Res<String> {
        let idx = self.where_()?;
        let what = match (took.len(), took.iter().all(|k| idx.contains_key(k))) {
            (1, _) if took[0].0 != "decision" => "a write to it".to_string(),
            (1, false) => "a call on it".to_string(),
            (1, true) => "a write to a call on it".to_string(),
            (n, false) => format!("{n} calls on it"),
            (n, true) => format!("writes to {n} calls on it"),
        };
        Ok(self.took_state(what))
    }

    /// The criteria a write git took out links to a question that the record on disk does not link there (the f13g
    /// review): taken out, they drop out of the question's reading, so it names them.
    pub fn taken_links_to(&self, question: &str) -> Res<Vec<Key>> {
        let t = self.taken_out()?;
        if t.is_empty() {
            return Ok(Vec::new());
        }
        let links = |f: &Dict| matches!(f.get("question_links"), Some(Value::List(l)) if l.iter().any(|x| x.get_str("question") == Some(question) && x.get("retracted").map_or(true, |r| r.is_null())));
        let on_disk: HashSet<String> = self.inquiry_criterion_ids(question)?;
        let mut out: Vec<Key> = Vec::new();
        for w in t.lines.iter().flat_map(|l| l.writes.iter()) {
            if w.key.0 == "criterion" && !out.contains(&w.key) && !on_disk.contains(&w.key.1) && w.form.as_ref().map_or(false, |f| links(f)) {
                out.push(w.key.clone());
            }
        }
        Ok(out)
    }

    /// The runs and analyses of an experiment git took out and rb kept, that are no longer on disk (S4): an experiment
    /// with none left on disk is not "planned", since rb keeps them. (runs, analyses)
    pub fn taken_evidence_of(&self, exp: &str) -> Res<(usize, usize)> {
        let t = self.taken_out()?;
        if t.is_empty() {
            return Ok((0, 0));
        }
        let idx = self.where_()?;
        let mut gone: HashSet<&Key> = HashSet::new();
        for w in t.lines.iter().flat_map(|l| l.writes.iter()) {
            if (w.key.0 == "run" || w.key.0 == "analysis") && !idx.contains_key(&w.key) && Ledger::path_experiment(&w.key.0, Path::new(&w.path.replace('\\', "/"))).as_deref() == Some(exp) {
                gone.insert(&w.key);
            }
        }
        let runs = gone.iter().filter(|k| k.0 == "run").count();
        Ok((runs, gone.len() - runs))
    }

    /// An experiment's state in words: "planned" where rb keeps runs git took out says those (S4).
    pub fn experiment_state_said(&self, exp: &Experiment, state: &str) -> Res<String> {
        if state == UNDETERMINED_STATE {
            let (_, took) = self.traced_calls(|| self.experiment_state_now(exp, None))?;
            return self.took_calls_state(&if took.is_empty() { vec![("experiment".to_string(), exp.id.clone())] } else { took });
        }
        if state != "planned" {
            return Ok(state.to_string());
        }
        Ok(match self.taken_evidence_of(&exp.id)? {
            (0, 0) => state.to_string(),
            (r, a) => self.took_state(taken_what(r, a)),
        })
    }

    /// The sentence said beside what git took out where the record also holds records in neither the log nor rb's copy
    /// (S6): an older rb's write between two of rb's own, or a hand's. rb doctor --restore sets them aside, so they are
    /// named before it does. None where there are none, or where git took nothing out.
    pub fn taken_unkept(&self) -> Res<Option<String>> {
        if self.taken_out()?.is_empty() {
            return Ok(None);
        }
        let unkept: Vec<String> = self
            .tampered()?
            .iter()
            .filter(|t| t.get_str("why").map_or(false, |w| w.ends_with("log.jsonl has no record of it")))
            .map(|t| format!("{} {}", t.get_str("kind").unwrap_or("record"), t.get("id").map(pyjson::py_str).unwrap_or_default()))
            .collect();
        if unkept.is_empty() {
            return Ok(None);
        }
        let [records, _, _, them] = crate::shell::strings::took_fill(unkept.len());
        let ids = crate::assess::first_few(&unkept, 6, ", ");
        Ok(Some(if unkept.len() == 1 {
            crate::shell::strings::evidence("took_unkept_one", &[("ids", ids)])
        } else {
            crate::shell::strings::evidence("took_unkept", &[("records", records), ("ids", ids), ("them", them)])
        }))
    }

    /// Whether a record file holds only writes git took out, so no read reads it (where_ leaves it out).
    pub fn took_hidden(&self, key: &Key) -> bool {
        self.took_hidden.borrow().iter().any(|(k, _)| k == key)
    }

    /// Put back what git took out, from the kept copy: the log lines, in the order rb wrote them, then each record's
    /// file as rb last wrote it. One entry per record, with its path.
    pub(super) fn put_back(&self, who: &Actor) -> Res<Vec<Dict>> {
        let t = self.taken_out()?;
        if t.is_empty() {
            return Ok(Vec::new());
        }
        let path_of = |key: &Key| -> String {
            t.lines.iter().rev().flat_map(|l| l.writes.iter()).find(|w| w.key == *key).map(|w| w.path.clone()).unwrap_or_default()
        };
        if let Some(name) = t.pop() {
            return Ok(t
                .records
                .iter()
                .map(|k| crate::dict! {"kind" => k.0.clone(), "id" => k.1.clone(), "path" => path_of(k), "done" => format!("left as it is: git stash pop {name} puts it back")})
                .collect());
        }
        let logp = self.dir.join("log.jsonl");
        let mut text: String = String::new();
        let had = crate::dry::read(&logp).unwrap_or_default();
        if !had.is_empty() && !had.ends_with(b"\n") {
            text.push('\n');
        }
        for l in &t.lines {
            text.push_str(&l.line);
            text.push('\n');
        }
        crate::dry::append(&logp, text.as_bytes()).map_err(|e| RBError::could_not_write(&e, &logp))?;
        *self.log_cache.borrow_mut() = None;
        *self.hashes.borrow_mut() = None;
        self.hashes_stamp.set(None);
        let last = self.last_writes()?;
        let mut done: Vec<Dict> = Vec::new();
        for key in &t.records {
            if t.lines.iter().any(|l| design_confirmed(&l.line).as_ref() == Some(key)) && !t.lines.iter().flat_map(|l| l.writes.iter()).any(|w| w.key == *key) {
                done.push(crate::dict! {"kind" => key.0.clone(), "id" => key.1.clone(), "path" => self.rel(&logp), "done" => crate::shell::strings::evidence("took_restored", &[])});
                continue;
            }
            let Some(entry) = last.get(key) else { continue };
            let sha = entry.get_str("sha256").unwrap_or("").to_string();
            let Some(w) = t.lines.iter().rev().flat_map(|l| l.writes.iter()).find(|w| w.key == *key && w.sha == sha) else { continue };
            let form = match w.form.clone() {
                Some(f) => Some(f),
                None => self.stored_form(&key.0, &key.1, &sha, Some(&w.path))?,
            };
            let target = self.root.join(&w.path);
            let now = self.in_(&key.0, &target).ok().and_then(|items| items.iter().find(|(i, _)| *i == key.1 || key.0 == "project").map(|(_, f)| f.clone()));
            let same = now.map_or(false, |f| rbyaml::content_sha(&Value::Dict(f)).ok().as_deref() == Some(sha.as_str()));
            if !same {
                let Some(form) = form else {
                    done.push(crate::dict! {"kind" => key.0.clone(), "id" => key.1.clone(), "path" => w.path.clone(), "done" => "left as it is: there is no copy of rb's last write here or in git history"});
                    continue;
                };
                self.put(&key.0, &key.1, &target, ordered(Self::meta_of(&key.0), &form))?;
                self.stash(&form, &sha);
            }
            done.push(crate::dict! {"kind" => key.0.clone(), "id" => key.1.clone(), "path" => w.path.clone(), "done" => crate::shell::strings::evidence("took_restored", &[])});
        }
        self.forget_all();
        self.log(who, "restore", "log", "log.jsonl", None, None, crate::dict! {"put_back" => t.lines.len() as i64})?;
        Ok(done)
    }

    /// Drop rb's kept copy of what git took out, when the user meant to undo it: each file that holds only such writes
    /// is set aside as <file>.outside-rb. Returns the records and the files set aside.
    pub fn forget_taken(&self) -> Res<(Vec<Key>, Vec<String>)> {
        let who = self.actor()?;
        self.locked(|| {
            let t = self.taken_out()?;
            if t.is_empty() {
                return Ok((Vec::new(), Vec::new()));
            }
            self.where_()?;
            let hidden = self.took_hidden.borrow().clone();
            let mut aside: Vec<String> = Vec::new();
            for (_, p) in &hidden {
                if crate::dry::exists(p) {
                    let a = self.set_aside(p);
                    if !aside.contains(&a) {
                        aside.push(a);
                    }
                }
            }
            if !crate::dry::active() {
                let hashes: Vec<Value> = t.lines.iter().map(|l| Value::Str(line_hash(&l.line))).collect();
                let dir = crate::journal::journal_dir(&self.root);
                let _ = crate::dry::append(&dir.join(KEPT_FILE), pyjson::jsonl_line(&crate::obj! {"at" => now(), "forget" => Value::List(hashes)}).as_bytes());
            }
            self.forget_all();
            let ids: Vec<Value> = t.records.iter().map(|k| Value::Str(k.1.clone())).collect();
            self.log(&who, "forget", "log", "log.jsonl", None, None, crate::dict! {"forgot" => Value::List(ids), "moved_aside" => Value::List(aside.iter().cloned().map(Value::Str).collect())})?;
            Ok((t.records.clone(), aside))
        })
    }

    /// Forget everything worked out from the log and the files, after a write that did not go through append_log.
    fn forget_all(&self) {
        *self.log_cache.borrow_mut() = None;
        *self.hashes.borrow_mut() = None;
        self.hashes_stamp.set(None);
        *self.index.borrow_mut() = None;
        *self.tamper.borrow_mut() = None;
        *self.git_took.borrow_mut() = None;
        self.edited.borrow_mut().clear();
        self.finding_cache.borrow_mut().clear();
    }

    /// What git took out, as every surface says it (the `taken_out` key): the count, the records, the stash, the line.
    pub fn taken_out_value(&self) -> Value {
        let Ok(t) = self.taken_out() else { return Value::Null };
        if t.is_empty() {
            return Value::Null;
        }
        let records: Vec<Value> = t.records.iter().map(|(k, i)| crate::obj! {"kind" => k.clone(), "id" => i.clone()}).collect();
        crate::obj! {
            "count" => t.records.len() as i64, "records" => Value::List(records),
            "stash" => Value::opt_str(t.stash.as_ref().map(|(s, _)| s.clone())), "pop" => t.pop().is_some(),
            "said" => crate::shell::strings::taken_out_line(t.records.len(), t.stash.as_ref().map(|(s, p)| (s.as_str(), *p)))
        }
    }
}

/// The blobs `git cat-file --batch` gives for `<rev>:<path>` lines, in order, None for what git does not have.
fn cat_blobs(root: &Path, input: &str) -> Vec<Option<String>> {
    let Ok(r) = sources::git_full(root, &["cat-file", "--batch"], Some(input.as_bytes())) else { return Vec::new() };
    let out = r.stdout;
    let mut blobs = Vec::new();
    let mut i = 0;
    while i < out.len() {
        let Some(nl) = out[i..].iter().position(|b| *b == b'\n') else { break };
        let head = String::from_utf8_lossy(&out[i..i + nl]).into_owned();
        i += nl + 1;
        let parts: Vec<&str> = head.split(' ').collect();
        if parts.len() != 3 {
            blobs.push(None);
            continue;
        }
        {
            let Ok(size) = parts[2].parse::<usize>() else { break };
            let end = (i + size).min(out.len());
            blobs.push((parts[1] == "blob").then(|| String::from_utf8_lossy(&out[i..end]).into_owned()));
            i = end + 1;
        }
    }
    blobs
}

/// Whether a metric named `id` with these aliases is the one `name` names.
pub fn metric_names(id: &str, aliases: &[String], name: &str) -> bool {
    name == id || aliases.iter().any(|a| a == name)
}

/// What a reading asked the record for (Ledger::traced): the records it loaded or looked up by id, the kinds it read
/// whole, the parts it read of an owner, the metric names it looked up in the catalogue (and whether it read the alias
/// map), the resources whose exposures it read, and the subjects whose calls it read.
#[derive(Clone, Debug, Default)]
pub struct Reads {
    pub keys: HashSet<Key>,
    pub ids: HashSet<String>,
    pub kinds: HashSet<String>,
    pub parts: HashSet<(String, Option<String>)>,
    pub metrics: HashSet<String>,
    pub aliases: bool,
    /// read which analysis supersedes which, in any experiment
    pub superseders: bool,
    pub eval_on: HashSet<String>,
    pub subjects: HashSet<String>,
    pub prefixes: HashSet<String>,
    /// inside a read the record answers by a rule of its own (read_as): the records it looks through are not noted
    pub quiet: u32,
}

impl Reads {
    pub fn key(&mut self, kind: &str, id: &str) {
        self.keys.insert((kind.to_string(), id.to_string()));
    }
    pub fn id(&mut self, id: &str) {
        if self.quiet == 0 {
            self.ids.insert(id.to_string());
        }
    }
    pub fn kind(&mut self, kind: &str) {
        if self.quiet == 0 {
            self.kinds.insert(kind.to_string());
        }
    }
    pub fn part(&mut self, kind: &str, owner: Option<&str>) {
        if self.quiet == 0 && kind != "note" {
            self.parts.insert((kind.to_string(), owner.map(str::to_string)));
        }
    }
    pub fn metric(&mut self, name: &str) {
        self.metrics.insert(name.to_string());
    }
    pub fn aliases(&mut self) {
        self.aliases = true;
    }
    pub fn superseders(&mut self) {
        self.superseders = true;
    }
    pub fn eval_on(&mut self, resource: &str) {
        self.eval_on.insert(resource.to_string());
        self.keys.insert(("resource".to_string(), resource.to_string()));
    }
    pub fn subject(&mut self, subject: &str) {
        self.subjects.insert(subject.to_string());
    }
    pub fn subject_prefix(&mut self, prefix: &str) {
        self.prefixes.insert(prefix.to_string());
    }
    /// Another reading's reads, as this one's (a reading made of others).
    pub fn extend(&mut self, o: &Reads) {
        self.keys.extend(o.keys.iter().cloned());
        self.ids.extend(o.ids.iter().cloned());
        self.kinds.extend(o.kinds.iter().cloned());
        self.parts.extend(o.parts.iter().cloned());
        self.metrics.extend(o.metrics.iter().cloned());
        self.aliases |= o.aliases;
        self.superseders |= o.superseders;
        self.eval_on.extend(o.eval_on.iter().cloned());
        self.subjects.extend(o.subjects.iter().cloned());
        self.prefixes.extend(o.prefixes.iter().cloned());
    }
}

/// "3 runs", "1 analysis", "2 runs and 1 analysis": what git took out of an experiment's evidence.
pub fn taken_what(runs: usize, analyses: usize) -> String {
    let r = if runs == 1 { "1 run".to_string() } else { format!("{runs} runs") };
    let a = if analyses == 1 { "1 analysis".to_string() } else { format!("{analyses} analyses") };
    match (runs, analyses) {
        (_, 0) => r,
        (0, _) => a,
        _ => format!("{r} and {a}"),
    }
}
