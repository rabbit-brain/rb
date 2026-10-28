//! Whose call a decision is: worked out on every read from what rb keeps (who recorded it and the log line of a
//! confirmation), never stored. A call recorded in an agent session is proposed, not made, until the user confirms it
//! with one command in their own terminal, and so is a past call one gives, whatever document it cites: the session
//! that reconstructs the past cannot attest it. What git shows of that document is a fact rb says beside it.

use super::*;

/// The status items of a call that waits for a confirmation: listed in a place of their own, never as a call.
pub const PENDING_CODES: &[&str] = &["call_proposed", "past_call_unconfirmed"];

/// A decision's authority. `state` is direct (recorded outside an agent session), confirmed (the user's confirmation of
/// a call recorded in one), proposed (a new call recorded in an agent session) or past_unconfirmed (a past call
/// recorded in one, its basis saying what git shows of the document it cites). The first two are calls; the other two
/// change nothing until they are confirmed. documented is no longer given: JSON keeps the value a reader may know.
#[derive(Debug, Clone, PartialEq)]
pub struct Authority {
    pub state: &'static str,
    /// what the state rests on, in words that name no writer
    pub basis: String,
    /// a confirmation: the decision it confirms
    pub confirms: Option<String>,
    /// a call proposed, or a past call mentioned, that was confirmed since: the confirmation
    pub confirmed_by: Option<String>,
    /// a call proposed and answered since by a call made on the same subject
    pub answered_by: Option<String>,
    /// still waiting for a confirmation: listed apart from the calls, with the command that confirms it
    pub pending: bool,
}

impl Authority {
    fn new(state: &'static str, basis: String) -> Authority {
        Authority { state, basis, confirms: None, confirmed_by: None, answered_by: None, pending: false }
    }

    /// Whether the decision is a call: it changes what it is made on (a question's, an experiment's or a setting's
    /// state, a finding's review).
    pub fn is_call(&self) -> bool {
        matches!(self.state, "direct" | "confirmed")
    }

    pub fn to_value(&self) -> Value {
        let mut d = crate::dict! {"state" => self.state, "basis" => self.basis.clone(), "pending" => self.pending};
        for (k, v) in [("confirms", &self.confirms), ("confirmed_by", &self.confirmed_by), ("answered_by", &self.answered_by)] {
            if let Some(v) = v {
                d.insert(k.into(), Value::Str(v.clone()));
            }
        }
        Value::Dict(d)
    }
}

/// What a past park or stop says of runs its experiment produced after the day it gives (runs_after_past_call).
pub fn runs_after_said(subject: &str, when: &str, runs: &[String]) -> String {
    let day: String = when.trim().chars().take(10).collect();
    match runs.len() {
        0 => String::new(),
        1 => format!(" · 1 run of {subject} was produced after {day}, the day given for this call: {}", runs[0]),
        n => format!(" · {n} runs of {subject} were produced after {day}, the day given for this call: {}", crate::assess::first_few(runs, 4, ", ")),
    }
}

/// The same, for a decision as rb status gives it (`runs_after`).
pub fn runs_after_of(d: &Value) -> String {
    let runs: Vec<String> = d.get("runs_after").and_then(Value::as_list).map(|l| l.iter().map(crate::pyjson::py_str).collect()).unwrap_or_default();
    let when = d.get("past").and_then(|p| p.get_str("when")).unwrap_or("");
    runs_after_said(d.get_str("subject").unwrap_or(""), when, &runs)
}

/// When a past call was made, as given: its date as said, or that nobody said.
pub fn past_when(p: &Past) -> String {
    if p.when.trim().is_empty() || p.when == "unknown" {
        "date unknown".into()
    } else {
        p.when.clone()
    }
}

impl Ledger {
    /// The record's first write (rb init, rb clone, rb reconcile --apply): its log line.
    pub fn first_write(&self) -> Res<Option<Dict>> {
        Ok(self.log_entries()?.iter().find(|r| !is_session_line(r)).cloned())
    }

    /// Every decision's authority, by id, worked out once per state of the record.
    pub fn authorities(&self) -> Res<Rc<HashMap<String, Authority>>> {
        let all = self.all::<Decision>()?;
        if let Some(hit) = self.objs.borrow().get("authority") {
            if let Ok(v) = hit.clone().downcast::<HashMap<String, Authority>>() {
                return Ok(v);
            }
        }
        // a confirmation's log line names the decision it confirms; who made a decision is who rb wrote it for, which a
        // by: edited in its file, or adopted since, does not change
        let mut confirms: HashMap<String, String> = HashMap::new();
        let mut written_by: HashMap<String, String> = HashMap::new();
        for row in self.log_entries()?.iter() {
            if row.get_str("op") == Some("decide") && row.get_str("kind") == Some("decision") {
                if let (Some(id), Some(target)) = (row.get_str("id"), row.get("detail").and_then(|d| d.get_str("confirms"))) {
                    confirms.entry(id.to_string()).or_insert_with(|| target.to_string());
                }
                if let (Some(id), Some(actor)) = (row.get_str("id"), row.get_str("actor")) {
                    written_by.entry(id.to_string()).or_insert_with(|| actor.to_string());
                }
            }
        }
        let start = self.first_write()?;
        let mut out: HashMap<String, Authority> = HashMap::new();
        for d in all.iter() {
            let a = if written_by.get(&d.id).unwrap_or(&d.by).starts_with("human:") {
                match confirms.get(&d.id) {
                    Some(target) => {
                        let mut a = Authority::new("confirmed", format!("confirms {target}"));
                        a.confirms = Some(target.clone());
                        a
                    }
                    None => Authority::new("direct", "recorded outside an agent session".into()),
                }
            } else {
                match &d.past {
                    None => Authority::new("proposed", format!("waits for one confirmation: rb confirm {}", d.id)),
                    // the document check is what rb found, said beside the call; it never stands in for the user
                    Some(p) => match &p.source {
                        None => Authority::new("past_unconfirmed", "given with no document".into()),
                        Some(src) => Authority::new("past_unconfirmed", self.documented(d, src, start.as_ref()).unwrap_or_else(|why| why)),
                    },
                }
            };
            out.insert(d.id.clone(), a);
        }
        // a confirmation rb wrote confirms the call it names, once
        for d in all.iter() {
            let Some(target) = out.get(&d.id).and_then(|a| a.confirms.clone()) else { continue };
            if self.outside_rb("decision", &d.id)? {
                continue;
            }
            if let Some(t) = out.get_mut(&target) {
                if !t.is_call() && t.confirmed_by.is_none() {
                    t.confirmed_by = Some(d.id.clone());
                }
            }
        }
        // a proposal is answered by a call made on its subject after it
        for d in all.iter() {
            if out.get(&d.id).map_or(true, |a| a.state != "proposed" || a.confirmed_by.is_some()) {
                continue;
            }
            let mut answered = None;
            for e in all.iter() {
                if e.subject == d.subject
                    && e.past.is_none()
                    && e.retracted.is_none()
                    && out.get(&e.id).map_or(false, Authority::is_call)
                    && crate::pytime::before(Some(&d.at), Some(&e.at))
                    && !self.outside_rb("decision", &e.id)?
                {
                    answered = Some(e.id.clone());
                }
            }
            if let Some(a) = out.get_mut(&d.id) {
                a.answered_by = answered;
            }
        }
        for d in all.iter() {
            let waiting = out.get(&d.id).map_or(false, |a| !a.is_call() && a.confirmed_by.is_none() && a.answered_by.is_none());
            let pending = waiting && d.retracted.is_none() && !self.outside_rb("decision", &d.id)?;
            if let Some(a) = out.get_mut(&d.id) {
                a.pending = pending;
            }
        }
        let rc = Rc::new(out);
        self.objs.borrow_mut().insert("authority", rc.clone() as Rc<dyn Any>);
        Ok(rc)
    }

    /// The runs of the experiment a past park or stop is on that were produced after the day the call gives: after
    /// that day's end in every time zone. Nothing for any other call, or one dated in words.
    pub fn runs_after_past_call(&self, d: &Decision) -> Res<Vec<String>> {
        let Some(p) = d.past.as_ref().filter(|_| matches!(d.action.as_str(), "park" | "stop")) else { return Ok(Vec::new()) };
        let day: String = p.when.trim().chars().take(10).collect();
        let Ok(day) = chrono::NaiveDate::parse_from_str(&day, "%Y-%m-%d") else { return Ok(Vec::new()) };
        // the day ends at 12:00 UTC the next day where it ends last
        let end = (day + chrono::Duration::days(1)).and_hms_opt(12, 0, 0).map(|t| t.and_utc().timestamp()).unwrap_or(i64::MAX);
        Ok(self
            .standing_runs()?
            .iter()
            .filter(|r| r.experiment == d.subject)
            .filter(|r| r.produced_at.as_deref().and_then(crate::pytime::parse_iso).map_or(false, |t| t.timestamp() > end))
            .map(|r| r.id.clone())
            .collect())
    }

    /// One decision's authority.
    pub fn authority(&self, id: &str) -> Res<Option<Authority>> {
        Ok(self.authorities()?.get(id).cloned())
    }

    /// Whether a decision confirms a call a session recorded, so its reason is that session's.
    pub fn confirms(&self, id: &str) -> Res<bool> {
        Ok(self.authorities()?.get(id).map_or(false, |a| a.confirms.is_some()))
    }

    /// The calls that wait for a confirmation, oldest first: live, what rb wrote, neither confirmed nor answered since.
    pub fn pending_calls(&self) -> Res<Vec<(Decision, Authority)>> {
        let auth = self.authorities()?;
        Ok(self.all::<Decision>()?.iter().filter_map(|d| auth.get(&d.id).filter(|a| a.pending).map(|a| (d.clone(), a.clone()))).collect())
    }

    /// Where what a waiting call was recorded on reads otherwise now: one line per criterion whose rule moved since.
    pub fn rule_moved(&self, d: &Decision) -> Res<Vec<String>> {
        let mut out = Vec::new();
        for (cid, then) in &d.basis {
            if self.newer_only("criterion", cid) {
                continue;
            }
            let Ok(c) = self.load::<Criterion>(cid) else { continue };
            let now = crate::assess::basis_of_finding(&self.finding(&c)?);
            let (was, is) = (self.basis_rule(cid, then), self.basis_rule(cid, &now));
            if was != is {
                out.push(format!("{cid} read {was} when this call was recorded; it reads {is} now."));
            }
        }
        Ok(out)
    }

    /// What git shows of the document a past call is given from: Ok("the quote is in <file>:<line>, committed <day>
    /// (<commit>), before the record began"), with why rb cannot tell who wrote it when the record began in the chat
    /// that cites it; Err(why not, in one line that names no writer) otherwise. A fact beside the call, never its
    /// authority.
    pub fn documented(&self, d: &Decision, src: &CallSource, start: Option<&Dict>) -> Result<String, String> {
        let at = format!("{}:{}", src.path, src.line);
        self.states_a_call(d, src).map_err(|why| format!("the quote at {at} {why}"))?;
        let Some(start) = start else { return Err("the record has no first write to date it against".into()) };
        // a session that began the record may have written the file before it did
        let began_here = start.get_str("actor").map_or(false, |a| a.starts_with("agent:")) && {
            let began = start.get_str("session");
            began.is_none() || d.session.is_none() || began == d.session.as_deref()
        };
        let Some((top, rel)) = git_path(&self.root, &src.path) else { return Err(format!("{} is not in a git repository, so rb cannot tell when it was written", src.path)) };
        let Some(commit) = src.commit.as_deref() else {
            let tracked = sources::git_bytes(&top, &["ls-files", "--error-unmatch", "--", &rel]).is_some();
            return Err(if tracked {
                format!("{} as last committed, with no change since, did not hold the quote when the call was recorded, so rb cannot tell when it was written", src.path)
            } else {
                format!("{} is not in git, so rb cannot tell when it was written", src.path)
            });
        };
        let short: String = commit.chars().take(12).collect();
        let Some(blob) = sources::git_bytes(&top, &["show", &format!("{commit}:{rel}")]) else {
            return Err(format!("git no longer has commit {short}, which held the quote at {}", src.path));
        };
        if !sources::holds_quote(&String::from_utf8_lossy(&blob), &src.quote) {
            return Err(format!("commit {short} does not hold the quote at {}", src.path));
        }
        let made = sources::git(&top, &["show", "-s", "--format=%cI", commit]).map(|s| s.trim().to_string()).filter(|s| !s.is_empty());
        let Some(made) = made else { return Err(format!("git no longer has commit {short}, which held the quote at {}", src.path)) };
        // git keeps whole seconds: a commit made in the second the record began may have come after it
        let second = |t: &str| crate::pytime::parse_iso(t).map(|t| t.timestamp());
        match (second(&made), start.get_str("at").and_then(second)) {
            (Some(c), Some(s)) if c < s => (),
            (Some(c), Some(s)) if c == s => {
                return Err(format!("{} first held the quote in commit {short}, made in the second the record began, so rb cannot tell which came first", src.path))
            }
            _ => {
                let day: String = made.chars().take(10).collect();
                return Err(format!("{} first held the quote in commit {short} ({day}), made after the record began", src.path));
            }
        }
        // the commit the record began on, as rb init kept it: a commit not in its history came after the record began
        if let Some(head) = start.get("detail").and_then(|x| x.get_str("head")) {
            if head.is_empty() {
                return Err(format!("the record began where git had no commit, so {} was committed after it", src.path));
            }
            if sources::git_bytes(&top, &["merge-base", "--is-ancestor", commit, head]).is_none() {
                return Err(format!("{} first held the quote in commit {short}, which is not in the history the record began on", src.path));
            }
        }
        let began = start.get_str("at").and_then(second).unwrap_or(i64::MIN);
        // git keeps the date its maker gave a commit: one that follows a commit made since the record began is no older
        let ancestors = sources::git(&top, &["log", "--format=%ct", commit]).unwrap_or_default();
        if ancestors.split_whitespace().any(|t| t.parse::<i64>().map_or(true, |t| t >= began)) {
            return Err(format!("{} first held the quote in commit {short}, which follows a commit made after the record began", src.path));
        }
        // once the record is committed, a commit older than it is in the history of the commit that first added it
        if let Some((_, log)) = git_path(&self.root, ".rb/log.jsonl") {
            let added = sources::git(&top, &["log", "--diff-filter=A", "--format=%H", "--", &log]).unwrap_or_default();
            if let Some(first) = added.split_whitespace().last() {
                if sources::git_bytes(&top, &["merge-base", "--is-ancestor", commit, first]).is_none() {
                    return Err(format!("{} first held the quote in commit {short}, which is not in the history of the commit that first added the record", src.path));
                }
            }
        }
        let day: String = made.chars().take(10).collect();
        let found = format!("the quote is in {at}, committed {day} ({short}), before the record began");
        Ok(if began_here { format!("{found}; whoever began the record cites it, so the file may have been written for the record") } else { found })
    }

    /// Whether a quote can state a call: Err(why not, as words that follow "the quote at <file>:<line>") when it is
    /// too short to say one, or names neither the subject nor any of its words.
    fn states_a_call(&self, d: &Decision, src: &CallSource) -> Result<(), String> {
        let said: Vec<String> = word_list(&src.quote);
        if said.len() < QUOTE_WORDS {
            let n = said.len();
            return Err(format!("has {n} word{}, too few to state a call ({QUOTE_WORDS} at least)", if n == 1 { "" } else { "s" }));
        }
        let quote = src.quote.to_lowercase();
        let names = self.subject_words(&d.subject);
        if quote.contains(&d.subject.to_lowercase()) || names.iter().any(|w| said.contains(w)) {
            return Ok(());
        }
        Err(format!("does not name {} or any of its words, so it does not say the call is on it", d.subject))
    }

    /// The words that name a subject: its id's, and its question's text, its experiment's title and hypothesis, its
    /// criterion's statement and metric, or its setting's name, past the words that name nothing.
    fn subject_words(&self, subject: &str) -> Vec<String> {
        let mut text = subject.replace(['-', '_', '/', '.'], " ");
        let (exp, setting) = match subject.split_once('/') {
            Some((e, n)) => (Some(e), Some(n)),
            None => (None, None),
        };
        let obj = match exp {
            Some(e) => self.get(e).ok(),
            None => self.get(subject).ok(),
        };
        match obj.map(|o| o.1) {
            Some(Obj::Question(q)) => text.push_str(&format!(" {}", q.text)),
            Some(Obj::Experiment(e)) if setting.is_none() => text.push_str(&format!(" {} {}", e.title, e.hypothesis)),
            Some(Obj::Criterion(c)) => text.push_str(&format!(" {} {}", c.statement, c.metric.replace(['.', '_'], " "))),
            _ => (),
        }
        word_list(&text).into_iter().filter(|w| w.chars().count() >= 3 && !w.chars().all(|c| c.is_ascii_digit()) && !NAME_NOTHING.contains(&w.as_str())).collect()
    }

    /// The document a past call is given from, checked as it is now: the file is in the project and holds the quote
    /// (on the line given, else the first line that does), and the earliest commit that held the quote at that path, as
    /// git shows the history of the branch checked out here. A quote the file does not hold is refused.
    pub fn call_source(&self, path: &str, line: Option<i64>, quote: &str) -> Res<CallSource> {
        let quote_ = crate::rbyaml::py_strip(quote).to_string();
        if quote_.is_empty() {
            return Err(RBError::msg("E_OBJECT_INVALID", "--quote is the words of the document that state the call; give them."));
        }
        if !sources::inside_project(&self.root, path) {
            return Err(RBError::msg_fix(
                "E_SOURCE_OUTSIDE",
                format!("{path} is outside the project, so nobody else can check it"),
                "copy it into the repository, commit it, and point --source at the copy",
            ));
        }
        if sources::in_state(&self.root, path) {
            return Err(RBError::msg("E_SOURCE_OUTSIDE", format!("{path} is rb's own record in .rb/, which cannot be where a call is stated")));
        }
        let full = self.root.join(path);
        let bytes = std::fs::read(&full).map_err(|e| RBError::msg("E_SOURCE_UNRESOLVED", format!("{path} could not be read: {}", crate::sys::os_words(&e))))?;
        let text = String::from_utf8_lossy(&bytes).into_owned();
        let lines: Vec<&str> = text.split('\n').collect();
        let holds = |no: usize| no >= 1 && no <= lines.len() && sources::holds_quote(lines[no - 1], &quote_);
        let found = match line {
            Some(no) if no >= 1 && holds(no as usize) => no,
            given => {
                let first = (1..=lines.len()).find(|no| holds(*no));
                let whole = sources::quote_places(&text, &quote_).next().map(|at| text[..at].matches('\n').count() + 1);
                match (given, first.or(whole)) {
                    (None, Some(no)) => no as i64,
                    (Some(no), Some(other)) => {
                        return Err(RBError::msg_fix(
                            "E_SOURCE_UNRESOLVED",
                            format!("{path}:{no} does not hold the quote; line {other} does"),
                            format!("--source {path}:{other}"),
                        ))
                    }
                    (_, None) => {
                        return Err(RBError::msg_fix(
                            "E_SOURCE_UNRESOLVED",
                            format!("{path} does not hold the quote {}", repr_str(&quote_)),
                            "quote the document's own words, as they are in the file",
                        ))
                    }
                }
            }
        };
        let commit = git_path(&self.root, path).and_then(|(top, rel)| earliest_holding(&top, &rel, &quote_, &bytes));
        Ok(CallSource { path: path.to_string(), line: found, quote: quote_, commit })
    }
}

/// The fewest words a quote that states a call has.
const QUOTE_WORDS: usize = 4;

/// Words that name no subject of their own.
const NAME_NOTHING: &[&str] = &[
    "the", "and", "for", "with", "that", "this", "from", "have", "has", "does", "did", "what", "which", "when", "will", "would", "should", "could",
    "there", "their", "they", "them", "been", "were", "was", "are", "not", "but", "into", "than", "then", "about", "our", "its", "can", "how",
    "why", "who", "all", "any", "more", "most", "less", "least", "much", "each", "per", "over", "under", "between", "versus", "adds", "add",
];

/// A text's words, in lower case; a setting written as name=value (P=4, d=1024) is one word.
fn word_list(text: &str) -> Vec<String> {
    static WORD: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    WORD.get_or_init(|| regex::Regex::new(r"[^\W_]+(?:=[^\W_]+)*").unwrap()).find_iter(&text.to_lowercase()).map(|m| m.as_str().to_string()).collect()
}

/// The git repository that holds a path under the project, and the path within it.
fn git_path(root: &Path, path: &str) -> Option<(PathBuf, String)> {
    let top = sources::git_top(root)?;
    let full = sources::resolve_path(&root.join(path));
    Some((top.clone(), sources::rel_to(&full, &top)?))
}

/// The earliest commit, in the history of what is checked out, from which the file at `rel` held the quote in every
/// commit up to the one checked out; None when the copy here is not what that commit holds. A quote dropped and put
/// back is as old as the commit that put it back.
fn earliest_holding(top: &Path, rel: &str, quote: &str, here: &[u8]) -> Option<String> {
    let lf = |b: &[u8]| String::from_utf8_lossy(b).replace("\r\n", "\n");
    let head = sources::git(top, &["rev-parse", "--verify", "-q", "HEAD^{commit}"])?;
    if lf(&sources::git_bytes(top, &["show", &format!("{}:{rel}", head.trim())])?) != lf(here) {
        return None;
    }
    let log = sources::git(top, &["log", "--full-history", "--reverse", "--format=%H", "--", rel])?;
    let mut since = None;
    for commit in log.split_whitespace() {
        let holds = sources::git_bytes(top, &["show", &format!("{commit}:{rel}")]).map_or(false, |blob| sources::holds_quote(&String::from_utf8_lossy(&blob), quote));
        since = if holds { since.or_else(|| Some(commit.to_string())) } else { None };
    }
    since
}
