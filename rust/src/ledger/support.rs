//! What a run rests on, worked out on every read from what the record keeps. A command reads as the one that made a
//! run's numbers only when it was given while the run was made, rb's journal saw it make them, or a log line written by
//! then records that launch; given later with none of these, it is kept as given and read as a command given later,
//! not observed. A file a run's numbers were read from is the run's own output only when it existed by the time the
//! run was produced, or it is the store itself (a database row rb read); a file written after the run is derived, and
//! never independent evidence because it exists. The record-start line counts the runs made before the record began.
//! Only what rb saw when a run was recorded is kept (`command_seen` on the receipt); everything else is computed.

use super::*;

/// How much later than a run's produced time its file, or the log that records its launch, may have been written and
/// still be the run's own: a file is written a moment after the numbers in it, and clocks differ. The same hour that
/// says a run was made before it was recorded.
pub const MARGIN_SECS: i64 = 3600;

/// How long before a run's produced time its launch log, or rb's journal's sight of its command, may be and still be
/// that run's: a long run writes its log at launch, days before its numbers.
pub const LAUNCH_SECS: i64 = 7 * 86400;

/// Where `at` falls against a run produced at `p`, when outside the window that makes it the run's: "after the run",
/// or "more than a week before the run". None inside it, or when either time cannot be read.
fn outside(at: &str, p: &str) -> Option<&'static str> {
    if after_by(at, p, MARGIN_SECS) {
        Some("after the run")
    } else if after_by(p, at, LAUNCH_SECS) {
        Some("more than a week before the run")
    } else {
        None
    }
}

/// Whether a file is documentation: a command written there is a recipe, not a record that it ran.
pub fn is_document(path: &str) -> bool {
    let name = path.rsplit(['/', '\\']).next().unwrap_or(path).to_lowercase();
    let ext = name.rsplit_once('.').map(|(_, e)| e).unwrap_or("");
    name.starts_with("readme") || ["md", "markdown", "mdx", "rst", "adoc", "asciidoc", "org", "tex", "ipynb", "html", "htm"].contains(&ext)
}

/// How rb knows the command a record gives made its numbers. `given` is at_the_time (given while it was made),
/// observed (rb's journal saw it run and make them), logged (a log line written by then records the launch) or later
/// (given after the run was made, with none of those).
#[derive(Debug, Clone, PartialEq)]
pub struct Given {
    pub text: String,
    pub given: &'static str,
    pub how: String,
}

impl Given {
    /// Whether the command reads as the one that made the numbers.
    pub fn made_it(&self) -> bool {
        self.given != "later"
    }

    pub fn to_value(&self) -> Value {
        crate::obj! {"text" => self.text.clone(), "given" => self.given, "how" => self.how.clone()}
    }

    /// The command as rb show prints it: as given, or as given later and not observed.
    pub fn line(&self) -> String {
        match self.given {
            "later" => format!("command given later, not observed: {}", self.text),
            "at_the_time" => format!("command (as given): {}", self.text),
            _ => format!("command (as given): {} ({})", self.text, self.how),
        }
    }

    /// The command as a screen's label and value: the label says which it is.
    pub fn label(&self) -> &'static str {
        if self.made_it() {
            "command (as given)"
        } else {
            "command given later, not observed"
        }
    }
}

/// Whether time `a` is more than `secs` seconds after time `b`; false when either cannot be read.
pub fn after_by(a: &str, b: &str, secs: i64) -> bool {
    match (crate::pytime::parse_iso(a), crate::pytime::parse_iso(b)) {
        (Some(a), Some(b)) => a.signed_duration_since(b) > chrono::Duration::seconds(secs),
        _ => false,
    }
}

/// A day as rb's text gives one: 2026-10-10.
pub fn day(at: &str) -> String {
    at.chars().take(10).collect()
}

/// Whether an analysis was made before it was recorded: when it was computed is unknown, or the time given for it is
/// more than an hour before it was recorded.
pub fn analysis_made_before(a: &Analysis) -> bool {
    if a.produced_basis == "unknown" {
        return true;
    }
    let at = a.receipt.as_ref().map(|r| r.at.clone()).filter(|x| !x.is_empty()).unwrap_or_else(|| a.created_at.clone());
    a.produced_basis == "reported" && a.produced_at.as_deref().map_or(false, |p| after_by(&at, p, MARGIN_SECS))
}

/// How rb knows `command` made the numbers of a record produced at `produced_at` (None: unknown), made before it was
/// recorded or not, with what rb saw when it was recorded (`seen`, the receipt's command_seen). None with no command.
pub fn command_given(command: Option<&str>, made_before: bool, seen: Option<&Value>, produced_at: Option<&str>) -> Option<Given> {
    let text = command.map(str::trim).filter(|c| !c.is_empty())?.to_string();
    let mut why_not = String::new();
    if let Some(s) = seen.filter(|s| s.truthy()) {
        match s.get_str("by") {
            Some("journal") => {
                let at = s.get_str("at").unwrap_or("");
                // a recipe run again today is not the command that made a run in May
                match produced_at.and_then(|p| outside(at, p)) {
                    Some(side) => why_not = format!("; rb's journal saw it run {}, {side}", day(at)),
                    None => return Some(Given { text, given: "observed", how: format!("rb journal run watched it make these numbers, {}", day(at)) }),
                }
            }
            Some("log") => {
                let (path, line) = (s.get_str("path").unwrap_or(""), s.get("line").map(pyjson::py_str).unwrap_or_default());
                let written = s.get_str("written").unwrap_or("");
                match produced_at {
                    _ if is_document(path) => why_not = format!("; {path} is documentation, which gives a recipe, not a launch"),
                    Some(p) if !written.is_empty() => match outside(written, p) {
                        None => return Some(Given { text, given: "logged", how: format!("{path}:{line} records this launch") }),
                        Some(side) => why_not = format!("; {path} was written {}, {side}", day(written)),
                    },
                    _ => why_not = format!("; {path}:{line} cannot show it was written by then"),
                }
            }
            _ => {}
        }
    }
    if !made_before {
        return Some(Given { text, given: "at_the_time", how: "given when the run was recorded, as it was made".into() });
    }
    let when = if produced_at.is_some() { "it was made before it was recorded" } else { "when it was made is unknown" };
    Some(Given { text, given: "later", how: format!("{when}, and neither rb's journal nor a log line written then shows this command made it{why_not}") })
}

/// How rb knows a run's command made its numbers. None with no command.
pub fn run_command(r: &Run) -> Option<Given> {
    command_given(r.receipt.command.as_deref(), made_before_recorded(r), r.receipt.extra.get("command_seen"), r.produced_at.as_deref().filter(|_| r.produced_basis != "unknown"))
}

/// How rb knows an analysis's command computed its numbers. None with no command.
pub fn analysis_command(a: &Analysis) -> Option<Given> {
    let rc = a.receipt.as_ref()?;
    command_given(rc.command.as_deref(), analysis_made_before(a), rc.extra.get("command_seen"), a.produced_at.as_deref().filter(|_| a.produced_basis != "unknown"))
}

/// When the file a run's numbers were read from was written, when that is after the run was produced: the file is
/// derived (an export, a copy, a transcript), not the run's own output. None for a run read from a database row (the
/// store itself), one whose produced time is unknown or rests on the file itself, and one with numbers typed in.
pub fn written_after(r: &Run) -> Option<String> {
    if r.receipt.row.is_some() || r.produced_basis == "unknown" {
        return None;
    }
    let produced = r.produced_at.as_deref().filter(|p| !p.is_empty())?;
    let written = r.receipt.produced.get("existed").and_then(|e| e.get_str("at")).filter(|w| !w.is_empty())?;
    after_by(written, produced, MARGIN_SECS).then(|| written.to_string())
}

/// What a run's file says when it was written after the run.
pub fn written_after_words(at: &str) -> String {
    format!("file written {}, after the run", day(at))
}

/// When the file a run's numbers were read from was written, as rb knows it: its earliest sign when recorded. None for a
/// row read from the database itself and for numbers typed in.
pub fn file_written(r: &Run) -> Option<String> {
    if r.receipt.row.is_some() {
        return None;
    }
    r.receipt.produced.get("existed").and_then(|e| e.get_str("at")).filter(|w| !w.is_empty()).map(str::to_string)
}

/// When the file a run's numbers were read from was written, for a run whose own time is unknown: rb cannot show the
/// file existed when the run was made. None otherwise.
pub fn written_when_unknown(r: &Run) -> Option<String> {
    file_written(r).filter(|_| r.produced_basis == "unknown")
}

/// What rb show and the card say of a run's file that rb cannot read as the run's own output, in full.
pub fn file_sentence(r: &Run) -> Option<String> {
    if let Some(at) = written_after(r) {
        return Some(format!("{}: the file its numbers were read from is not the run's own output", written_after_words(&at)));
    }
    written_when_unknown(r).map(|at| format!("file written {}; when the run was made is unknown, so rb cannot show the file is its own output", day(&at)))
}

/// The same, short, for a line that lists runs.
pub fn file_words(r: &Run) -> Option<String> {
    if let Some(at) = written_after(r) {
        return Some(written_after_words(&at));
    }
    written_when_unknown(r).map(|at| format!("file written {}; when the run was made is unknown", day(&at)))
}

/// A time as rb's text gives one, to the minute: 2026-05-07 20:07 UTC.
pub fn minute(at: &str) -> String {
    match crate::pytime::norm_time(at).and_then(|t| crate::pytime::parse_iso(&t)) {
        Some(t) => format!("{} UTC", crate::pytime::iso_minutes(&t.with_timezone(&chrono::Utc)).replace('T', " ")),
        None => at.to_string(),
    }
}

/// When a run was produced, in the words of what that rests on, and when it was recorded: "produced 2026-05-07 20:07
/// UTC (as reported) · recorded 2026-10-10 14:51 UTC".
pub fn produced_line(r: &Run) -> String {
    let recorded = minute(if r.receipt.at.is_empty() { &r.created_at } else { &r.receipt.at });
    let column = r.receipt.row.as_ref().and_then(|w| w.produced_at.as_ref()).map(|c| format!(", column {c}")).unwrap_or_default();
    let how = match r.produced_basis.as_str() {
        "reported" => "as reported",
        "journal" => "when rb's journal first saw its file",
        "git" => "git's first commit of its file",
        "file" => "its file's time",
        _ => "",
    };
    match r.produced_at.as_deref().filter(|p| !p.is_empty()) {
        _ if r.produced_basis == "unknown" => format!("produced: when is unknown · recorded {recorded}"),
        Some(_) if r.produced_basis == "recorded" => format!("produced and recorded {recorded}"),
        Some(p) => format!("produced {} ({how}{column}) · recorded {recorded}", minute(p)),
        None => format!("produced: when is unknown · recorded {recorded}"),
    }
}

/// What a run add says beside the run it records: a command kept as given later, a file written after the run, and a
/// file dated minutes ago with no --produced-at, which may hold an older run.
pub fn told_after_add(r: &Run, produced_given: bool, numbers_from: &[String]) -> Vec<String> {
    let mut out = Vec::new();
    if let Some(g) = run_command(r).filter(|g| !g.made_it()) {
        out.push(format!(
            "--command is kept as given and reads as a command given later, not observed: {}. A command from a README, a paper or a script is a recipe: put it in a note with its file and line. A log line written when it ran that records the launch is cited with --command-source <file>:<line>.",
            g.how
        ));
    }
    let file = numbers_from.first().cloned().or_else(|| r.files.first().map(|f| f.path.clone())).unwrap_or_default();
    if let Some(at) = written_after(r) {
        out.push(format!(
            "{file} was written {}, after the run (produced {}): it is not the run's own output, and rb reads it as a file written after the run. Where its numbers came from a database, read the row itself: rb run add {} --from <database> --table <table> --where <column>=<value>.",
            day(&at),
            day(r.produced_at.as_deref().unwrap_or("")),
            r.experiment
        ));
    }
    if let Some(at) = written_when_unknown(r).filter(|_| !numbers_from.is_empty()) {
        out.push(format!(
            "{file} was written {}, and when the run was made is unknown, so rb cannot show it is the run's own output. Say when it ran with --produced-at <time>, or read the store its numbers came from: rb run add {} --from <database> --table <table> --where <column>=<value>.",
            day(&at),
            r.experiment
        ));
    }
    if !produced_given && !numbers_from.is_empty() && r.receipt.row.is_none() && matches!(r.produced_basis.as_str(), "file" | "git") {
        if let (Some(made), Some(now)) = (r.produced_at.as_deref().and_then(crate::pytime::parse_iso), crate::pytime::parse_iso(&r.created_at)) {
            let mins = now.signed_duration_since(made).num_minutes();
            if (0..60).contains(&mins) {
                let ago = match mins {
                    0 => "less than a minute".to_string(),
                    1 => "1 minute".to_string(),
                    n => format!("{n} minutes"),
                };
                out.push(format!("{file} was written {ago} before this run was recorded, and rb dates the run by it. If it holds an older run, say when it ran: --produced-at <time>."));
            }
        }
    }
    out
}

/// When the record began, and how many of its runs were made before then.
#[derive(Debug, Clone, PartialEq)]
pub struct Start {
    pub at: String,
    /// standing runs made more than the margin before the record began
    pub before: usize,
    pub runs: usize,
    /// standing runs whose produced time is unknown
    pub unknown: usize,
}

impl Start {
    pub fn to_value(&self) -> Value {
        crate::obj! {"at" => self.at.clone(), "before" => self.before, "runs" => self.runs, "unknown" => self.unknown}
    }

    /// "Recorded since 2026-10-10, after 117 of its 119 runs were made; what came before it is Retrospective"
    pub fn words(&self) -> String {
        let after = match (self.before, self.runs) {
            (1, 1) => "after its one run was made".to_string(),
            (2, 2) => "after both its runs were made".to_string(),
            (b, n) if b == n => format!("after all {n} of its runs were made"),
            (1, n) => format!("after 1 of its {n} runs was made"),
            (b, n) => format!("after {b} of its {n} runs were made"),
        };
        let unknown = match self.unknown {
            0 => String::new(),
            1 => " (when 1 was made is unknown)".to_string(),
            n => format!(" (when {n} were made is unknown)"),
        };
        format!("Recorded since {}, {after}{unknown}; what came before it is Retrospective", day(&self.at))
    }
}

/// Whether a run was made before the record began at `began`: its produced time, when known, is more than the margin
/// earlier. None while when it was made is unknown.
pub fn made_before_start(r: &Run, began: &str) -> Option<bool> {
    if r.produced_basis == "unknown" {
        return None;
    }
    let made = r.produced_at.as_deref().filter(|p| !p.is_empty()).unwrap_or(&r.created_at);
    Some(after_by(began, made, MARGIN_SECS))
}

impl Ledger {
    /// When the record's first write was.
    pub fn began(&self) -> Res<Option<String>> {
        Ok(self.first_write()?.and_then(|w| w.get_str("at").map(str::to_string)).filter(|a| !a.is_empty()))
    }

    /// The record-start line's facts, while any standing run was made before the record began. None otherwise.
    pub fn record_start(&self) -> Res<Option<Start>> {
        let Some(at) = self.began()? else { return Ok(None) };
        let runs = self.standing_runs()?;
        let made: Vec<Option<bool>> = runs.iter().map(|r| made_before_start(r, &at)).collect();
        let before = made.iter().filter(|m| **m == Some(true)).count();
        if before == 0 {
            return Ok(None);
        }
        Ok(Some(Start { at, before, runs: runs.len(), unknown: made.iter().filter(|m| m.is_none()).count() }))
    }

    /// The earliest sign rb can vouch for of a file's bytes as they are: the journal's first sighting of them, git's
    /// first commit of them or the file's own last write. None for a file rb cannot read.
    pub fn earliest_sign(&self, path: &str) -> Option<String> {
        let full = sources::full_path(&self.root, path);
        let sha = sha256_bytes(&std::fs::read(&full).ok()?);
        let mut signs: Vec<String> = Vec::new();
        if let Some(Value::Dict(hit)) = crate::journal::load_state(&self.root).get("files").and_then(|f| f.get(path)).cloned() {
            if hit.get_str("sha") == Some(sha.as_str()) {
                signs.extend(hit.get_str("at").and_then(crate::pytime::norm_time));
            }
        }
        signs.extend(sources::first_committed(&self.root, path));
        signs.extend(crate::pytime::file_time(&full));
        signs.into_iter().min()
    }

    /// What rb saw of a command when a run is recorded: a run of it rb's journal watched that wrote the run's files or
    /// printed its numbers, or the log line given with --command-source, which must hold the command. A log line is
    /// kept with when its file was written, so a read says whether it was written by the time the run was made.
    pub fn command_seen(&self, command: Option<&str>, source: Option<&str>, files: &[FileRef], metrics: &Dict) -> Res<Option<Value>> {
        let Some(cmd) = command.map(str::trim).filter(|c| !c.is_empty()) else {
            if source.is_some() {
                return Err(RBError::msg("E_USAGE", "--command-source names the log line that records the run's --command; give --command too."));
            }
            return Ok(None);
        };
        if let Some(src) = source.map(str::trim).filter(|s| !s.is_empty()) {
            return self.logged_launch(cmd, src).map(Some);
        }
        let paths: HashSet<&str> = files.iter().map(|f| f.path.as_str()).collect();
        let same = |shown: &Dict| {
            !shown.is_empty()
                && shown.iter().all(|(k, v)| {
                    let Some(x) = v.as_f64() else { return false };
                    metrics.iter().any(|(m, y)| (m == k || m.rsplit('.').next() == Some(k.as_str())) && y.as_f64().map_or(false, |y| isclose(x, y, 1e-9, 1e-12)))
                })
        };
        for e in crate::journal::entries(&self.root, None).iter().rev() {
            if e.get_str("op") != Some("run") || !e.get_str("command").map_or(false, |c| crate::journal::same_command(c, cmd)) {
                continue;
            }
            let mt = |p: &str| std::fs::metadata(self.root.join(p)).ok().map(|md| crate::sys::mtime_ns(&md) as i64);
            let wrote = crate::journal::wrote_of(e, &mt).iter().any(|p| paths.contains(p.as_str()));
            let printed = matches!(e.get("metrics"), Some(Value::Dict(m)) if same(m));
            if wrote || printed {
                return Ok(Some(crate::obj! {"by" => "journal", "at" => e.get("at").cloned().unwrap_or(Value::Null)}));
            }
        }
        Ok(None)
    }

    /// The log line given for a run's launch (`<file>:<line>`, or `<file>` for the first line that holds it), checked:
    /// in the project, outside .rb/, and holding the command as given. A line that does not hold it is refused.
    fn logged_launch(&self, cmd: &str, src: &str) -> Res<Value> {
        let (path, line) = match src.rsplit_once(':') {
            Some((p, n)) if !p.is_empty() && n.parse::<i64>().is_ok() => (p.to_string(), Some(n.parse::<i64>().unwrap())),
            _ => (src.to_string(), None),
        };
        let path = crate::sys::typed(&path);
        // an absolute path inside the record's folder is kept from it, as every path the record keeps: so a page can
        // name a file inside a nested worktree by its path there, never by where the machine keeps it
        let path = match std::path::Path::new(&path).is_absolute() {
            true => sources::rel_to(&sources::resolve_path(std::path::Path::new(&path)), &sources::resolve_path(&self.root)).filter(|r| !r.is_empty()).unwrap_or(path),
            false => path,
        };
        if !sources::inside_project(&self.root, &path) {
            return Err(RBError::msg_fix(
                "E_SOURCE_OUTSIDE",
                format!("{path} is outside the project, so nobody else can check it"),
                "point --command-source at the log in the repository",
            ));
        }
        if sources::in_state(&self.root, &path) {
            return Err(RBError::msg("E_SOURCE_OUTSIDE", format!("{path} is rb's own record in .rb/, which records no launch")));
        }
        if is_document(&path) {
            return Err(RBError::msg_fix(
                "E_SOURCE_UNRESOLVED",
                format!("{path} is documentation: a command written there is a recipe, not a record that it ran"),
                "put the recipe in a note with its file and line; cite a log the run wrote with --command-source <file>:<line>",
            ));
        }
        let full = sources::full_path(&self.root, &path);
        let bytes = std::fs::read(&full).map_err(|e| RBError::msg("E_SOURCE_UNRESOLVED", format!("{path} could not be read: {}", crate::sys::os_words(&e))))?;
        let text = String::from_utf8_lossy(&bytes).into_owned();
        let lines: Vec<&str> = text.split('\n').collect();
        let holds = |no: usize| no >= 1 && no <= lines.len() && sources::holds_quote(lines[no - 1], cmd);
        let found = match line {
            Some(no) if holds(no as usize) => no,
            given => match (given, (1..=lines.len()).find(|no| holds(*no))) {
                (None, Some(no)) => no as i64,
                (Some(no), Some(other)) => {
                    return Err(RBError::msg_fix("E_SOURCE_UNRESOLVED", format!("{path}:{no} does not hold the command; line {other} does"), format!("--command-source {path}:{other}")))
                }
                (_, None) => {
                    return Err(RBError::msg_fix(
                        "E_SOURCE_UNRESOLVED",
                        format!("{path} does not hold the command {}", repr_str(cmd)),
                        "cite the log line that records this launch, with --command as it is written there",
                    ))
                }
            },
        };
        Ok(crate::obj! {
            "by" => "log", "path" => path.clone(), "line" => found, "sha256" => sha256_bytes(&bytes),
            "written" => self.earliest_sign(&path).map(Value::Str).unwrap_or(Value::Null)
        })
    }
}

impl Ledger {
    /// Each experiment's late criteria with no reading of their own yet, as one item. Where no more runs are expected
    /// (no gate, and no run made since the record began) it asks for nothing; where runs are expected, its command
    /// names both arms, since a reading of its own needs both run after the declaration.
    pub(crate) fn late_items(
        &self,
        items: &mut Vec<Dict>,
        late: IndexMap<String, Vec<(String, String, String)>>,
        experiments: &[Experiment],
        criteria: &[Criterion],
        runs: &[Run],
        retracted: &HashMap<String, String>,
    ) -> Res<()> {
        let began = self.began()?;
        for (exp, late) in late {
            if retracted.contains_key(&exp) || late.is_empty() {
                continue;
            }
            let gated = experiments.iter().any(|e| e.id == exp && (!e.if_holds.is_empty() || !e.if_not.is_empty()))
                || criteria.iter().any(|c| c.experiment == exp && c.role == "gate");
            let since = began.as_deref().map_or(true, |b| runs.iter().any(|r| r.experiment == exp && made_before_start(r, b) == Some(false)));
            let n = late.len();
            let (evidence, seen) = (late.iter().any(|l| l.1 == "after_evidence"), late.iter().any(|l| l.1 == "after_exposure"));
            let (their, its) = if n == 1 { ("its", "it reads") } else { ("their", "they read") };
            let own = if n == 1 { "its" } else { "their" };
            let after = match (evidence, seen) {
                (true, false) => format!("{their} evidence existed"),
                (false, true) => format!("the data {its} was seen"),
                _ => format!("{their} evidence existed or the data {its} was seen"),
            };
            let mut what = format!("{exp}: {n} {} declared after {after}", if n == 1 { "criterion was" } else { "criteria were" });
            let do_ = if gated || since {
                // a rule's timing is read on its subject's runs: its baseline's may have come first
                let subjects = if n == 1 { "its subject's" } else { "their subjects'" };
                what += &format!(": a reading of {own} own needs {subjects} runs made after the declaration");
                format!("{}   (run after the declaration)", late[0].2)
            } else {
                String::new()
            };
            let ids: Vec<String> = late.iter().map(|l| l.0.clone()).collect();
            super::views::item(items, "agent", "late", &exp, &what, &do_, vec![("criteria", Value::list_of_str(ids))]);
        }
        Ok(())
    }

    /// The items for what runs rest on: commands given later, files written after their runs, and words in the
    /// record's text about who wrote it.
    pub(crate) fn support_items(&self, items: &mut Vec<Dict>, runs: &[Run], analyses: &[Analysis]) -> Res<()> {
        let plural = |n: usize, one: &str, many: &str| if n == 1 { one.to_string() } else { many.to_string() };
        let mut later: Vec<String> = runs.iter().filter(|r| run_command(r).map_or(false, |g| !g.made_it())).map(|r| r.id.clone()).collect();
        let from_runs = later.len();
        later.extend(analyses.iter().filter(|a| a.retracted.is_none() && analysis_command(a).map_or(false, |g| !g.made_it())).map(|a| a.id.clone()));
        if let Some(first) = later.first().cloned() {
            let n = later.len();
            // rb says a record was made before it was recorded only where it knows when it was made
            let unknown = runs.iter().filter(|r| later.contains(&r.id) && r.produced_basis == "unknown").count()
                + analyses.iter().filter(|a| later.contains(&a.id) && a.produced_basis == "unknown").count();
            let when = match (unknown, n) {
                (0, 1) => "it was made before it was recorded".to_string(),
                (0, _) => "each was made before it was recorded".to_string(),
                (u, _) if u == n => format!("when {} made is unknown", plural(n, "it was", "each was")),
                _ => "each was made before it was recorded, or when is unknown".to_string(),
            };
            let what = match (from_runs, n - from_runs) {
                (r, 0) => format!("{r} {}", plural(r, "run gives", "runs give")),
                (0, a) => format!("{a} {}", plural(a, "analysis gives", "analyses give")),
                _ => format!("{n} runs and analyses give"),
            };
            super::views::item(
                items,
                "agent",
                "command_later",
                &first,
                &format!(
                    "{what} a command given later, not observed ({}): {when}, and nothing rb saw or a log line written then shows the command made it",
                    crate::assess::first_few(&later, 6, ", "),
                ),
                &format!("rb show {first}   (a command from a README, a paper or a script is a recipe: put it in a note with its file and line; a run's own log line is cited with rb run add --command-source <file>:<line>)"),
                vec![("records", Value::list_of_str(later.clone()))],
            );
        }
        let derived: Vec<&Run> = runs.iter().filter(|r| written_after(r).is_some()).collect();
        if let Some(first) = derived.first() {
            let ids: Vec<String> = derived.iter().map(|r| r.id.clone()).collect();
            let n = ids.len();
            super::views::item(
                items,
                "agent",
                "file_written_after",
                &first.id,
                &format!(
                    "{n} {} read from {} written after {} ran ({}): such a file is not the run's own output",
                    plural(n, "run was", "runs were"),
                    plural(n, "a file", "files"),
                    plural(n, "it", "they"),
                    crate::assess::first_few(&ids, 6, ", ")
                ),
                &format!("read the store the numbers came from: rb run add {} --from <database> --table <table> --where <column>=<value> --replaces {} --why \"read from the store itself\"", first.experiment, first.id),
                vec![("records", Value::list_of_str(ids.clone()))],
            );
        }
        let unknown: Vec<&Run> = runs.iter().filter(|r| written_when_unknown(r).is_some()).collect();
        if let Some(first) = unknown.first() {
            let ids: Vec<String> = unknown.iter().map(|r| r.id.clone()).collect();
            let n = ids.len();
            super::views::item(
                items,
                "agent",
                "file_run_time_unknown",
                &first.id,
                &format!(
                    "{n} {} read from {} while when {} made is unknown ({}): rb cannot show such a file is the run's own output",
                    plural(n, "run was", "runs were"),
                    plural(n, "a file", "files"),
                    plural(n, "it was", "they were"),
                    crate::assess::first_few(&ids, 6, ", ")
                ),
                &format!("read the store the numbers came from: rb run add {} --from <database> --table <table> --where <column>=<value> --replaces {} --why \"read from the store itself\"", first.experiment, first.id),
                vec![("records", Value::list_of_str(ids.clone()))],
            );
        }
        // rb's line names where the words are, never the writer ids they hold; the marks stay in the data
        let words = self.writer_words()?;
        if let Some((subject, places, n, look, data)) = words_item(&words) {
            let why = "the record keeps who made each write; a text says what it rests on: a file and line, or nothing";
            let what = format!("{n} {} who wrote {}, or how ({places})", plural(n, "text says", "texts say"), plural(n, "it", "them"));
            super::views::item(items, "agent", "writer_words", &subject, &what, &look.map(|l| format!("{l}   ({why})")).unwrap_or_default(), data);
        }
        // like writer_words: where the words are, never who wrote them
        let named = self.attributed_words()?;
        if let Some((subject, places, n, look, data)) = words_item(&named) {
            let why = "a text says what it rests on: a file and line, or nothing; words recalled from the user go in --words, shown as words (as given)";
            let what = format!("{n} {} the user or the chat as {} source ({places})", plural(n, "text names", "texts name"), plural(n, "its", "their"));
            super::views::item(items, "agent", "attributed_words", &subject, &what, &look.map(|l| format!("{l}   ({why})")).unwrap_or_default(), data);
        }
        Ok(())
    }

    /// The texts an agent wrote in the record that say who wrote them, or how, each with the words as written.
    pub fn writer_words(&self) -> Res<Vec<WriterWords>> {
        // the ids each writer goes by, from what the log keeps of its agent
        let mut ids: HashMap<String, Vec<String>> = HashMap::new();
        for row in self.log_entries()?.iter() {
            let (Some(actor), Some(agent)) = (row.get_str("actor"), row.get("agent")) else { continue };
            let have = ids.entry(actor.to_string()).or_default();
            for id in writer_ids_of(agent, actor) {
                if !have.contains(&id) {
                    have.push(id);
                }
            }
        }
        Ok(self.agent_texts(|by, text| {
            let theirs = ids.get(by).cloned().unwrap_or_else(|| writer_ids_of(&Value::Null, by));
            crate::plain::writer_marks(text, &theirs)
        })?)
    }

    /// The texts an agent wrote that name the user, or the chat, as their source, each with the words as written.
    pub fn attributed_words(&self) -> Res<Vec<WriterWords>> {
        self.agent_texts(|_, text| crate::plain::attribution_marks(text))
    }

    /// Each text an agent wrote that is in force, in the record or as a reason only the log keeps, with the marks `find`
    /// finds in it. A retracted record, note or part keeps only its retraction's reason, and a reason only the log keeps
    /// counts while no later write of the same kind replaced it, so taking the text back, or setting it again, clears it.
    fn agent_texts(&self, find: impl Fn(&str, &str) -> Vec<String>) -> Res<Vec<WriterWords>> {
        let mut out: Vec<WriterWords> = Vec::new();
        let seen: std::cell::RefCell<std::collections::HashSet<(String, String)>> = std::cell::RefCell::new(Default::default());
        let gone: std::cell::RefCell<std::collections::HashSet<String>> = std::cell::RefCell::new(Default::default());
        // (the record, where in it, what rb show opens, its writer, every text it holds, the texts in force)
        let mut check = |record: &str, place: &dyn Fn(&str) -> String, show: Option<&str>, by: &str, all: Vec<(&'static str, String)>, live: Vec<(&'static str, String)>, from_log: bool| {
            for (_, text) in &all {
                seen.borrow_mut().insert((record.to_string(), text.clone()));
            }
            if !by.starts_with("agent:") {
                return;
            }
            for (field, text) in live {
                let marks = find(by, &text);
                if !marks.is_empty() {
                    out.push(WriterWords { record: record.to_string(), place: place(field), show: show.map(str::to_string), marks, from_log });
                }
            }
        };
        let note = |n: &Note| match &n.retracted {
            Some(r) => vec![("why", r.why.clone())],
            None => vec![("text", n.text.clone())],
        };
        let every = |n: &Note| vec![("text", n.text.clone())].into_iter().chain(n.retracted.as_ref().map(|r| ("why", r.why.clone()))).collect::<Vec<_>>();
        for n in &self.project()?.notes {
            let place = n.id.as_ref().map_or_else(|| "a note on the project".to_string(), |id| format!("note {id} on the project"));
            check(n.id.as_deref().unwrap_or("project"), &|_| place.clone(), None, &n.by, every(n), note(n), false);
        }
        macro_rules! each {
            ($t:ty, $kind:expr, $by:ident) => {
                for x in self.all::<$t>()?.iter() {
                    let mut d = x.dump(false);
                    d.shift_remove("notes");
                    if x.retracted.is_some() {
                        gone.borrow_mut().insert(x.id.clone());
                    }
                    let live = crate::plain::prose($kind, &in_force(&d));
                    check(&x.id, &|f: &str| format!("{} {f}", x.id), Some(&x.id), &x.$by, crate::plain::prose($kind, &d), live, false);
                }
            };
        }
        // a question's wording is whoever last gave it: the latest rewording's writer, else whoever asked it
        for x in self.all::<Question>()?.iter() {
            let d = x.dump(false);
            if x.retracted.is_some() {
                gone.borrow_mut().insert(x.id.clone());
            }
            let by = self.rewordings(&x.id)?.last().map_or_else(|| x.created_by.clone(), |r| r.by.clone());
            let live = crate::plain::prose("question", &in_force(&d));
            check(&x.id, &|f: &str| format!("{} {f}", x.id), Some(&x.id), &by, crate::plain::prose("question", &d), live, false);
        }
        each!(Experiment, "experiment", created_by);
        for e in self.all::<Experiment>()?.iter() {
            for n in &e.notes {
                let live = if e.retracted.is_some() { Vec::new() } else { note(n) };
                let place = n.id.as_ref().map_or_else(|| format!("a note on {}", e.id), |id| format!("note {id} on {}", e.id));
                check(n.id.as_deref().unwrap_or(&e.id), &|_| place.clone(), Some(&e.id), &n.by, every(n), live, false);
            }
        }
        each!(Criterion, "criterion", created_by);
        each!(Run, "run", created_by);
        each!(Analysis, "analysis", created_by);
        each!(Decision, "decision", by);
        each!(Resource, "resource", created_by);
        // a reason only the log keeps (a gate's, a move's, a title's), as the latest write of its kind left it: a
        // confirmation keeps the reason it confirms
        let mut latest: IndexMap<(String, String, String, String), (String, String)> = IndexMap::new();
        for row in self.log_entries()?.iter() {
            let (Some(by), Some(op), Some(kind), Some(id)) = (row.get_str("actor"), row.get_str("op"), row.get_str("kind"), row.get_str("id")) else { continue };
            let detail = row.get("detail");
            if crate::plain::COPIED.contains(&op) || detail.map_or(false, |d| d.get("confirms").map_or(false, Value::truthy)) {
                continue;
            }
            let part = ["question", "setting", "variant", "note", "target"].iter().find_map(|k| detail.and_then(|d| d.get_str(k))).unwrap_or("").to_string();
            // a link withdrawn is a write of the link, as its filing was
            let op = if op == "unfile" { "file" } else { op };
            let why = detail.and_then(|d| d.get_str("why")).unwrap_or("").to_string();
            latest.insert((op.to_string(), kind.to_string(), id.to_string(), part), (by.to_string(), why));
        }
        for ((op, kind, id, _), (by, why)) in latest {
            // a reason its record keeps, or one of a record taken back, is not the log's alone
            if why.trim().is_empty() || seen.borrow().contains(&(id.clone(), why.clone())) || gone.borrow().contains(&id) {
                continue;
            }
            let show = (kind != "project").then_some(id.as_str());
            check(&id, &|_| format!("{id} {op} why"), show, &by, Vec::new(), vec![("why", why.clone())], true);
        }
        Ok(out)
    }
}

/// A record's form as it is in force: a part that was taken back (retracted) keeps only its retraction.
fn in_force(form: &Dict) -> Dict {
    fn walk(v: &Value) -> Value {
        match v {
            Value::Dict(d) if d.get("retracted").map_or(false, Value::truthy) => Value::Dict(crate::dict! {"retracted" => d.get("retracted").cloned().unwrap_or(Value::Null)}),
            Value::Dict(d) => Value::Dict(d.iter().map(|(k, x)| (k.clone(), walk(x))).collect()),
            Value::List(l) => Value::List(l.iter().map(walk).collect()),
            x => x.clone(),
        }
    }
    match walk(&Value::Dict(form.clone())) {
        Value::Dict(d) => d,
        _ => Dict::new(),
    }
}

/// What the open item for texts an agent wrote that a check found words in says: its subject, the places that hold
/// them and how many, the command that opens the first a record holds (none for a reason only the log keeps, which no
/// write but the next of its kind replaces), and its data. It names no writer.
#[allow(clippy::type_complexity)]
fn words_item(words: &[WriterWords]) -> Option<(String, String, usize, Option<String>, Vec<(&'static str, Value)>)> {
    let first = words.first()?;
    let mut shown: Vec<String> = Vec::new();
    for w in words {
        if !shown.contains(&w.place) {
            shown.push(w.place.clone());
        }
    }
    let texts: Vec<Value> = words.iter().map(|w| crate::obj! {"record" => w.record.clone(), "place" => w.place.clone(), "marks" => Value::list_of_str(w.marks.clone())}).collect();
    let mut ids: Vec<String> = words.iter().map(|w| w.record.clone()).collect();
    ids.dedup();
    let look = words.iter().find(|w| !w.from_log).map(|w| w.show.as_ref().map_or_else(|| "rb status".to_string(), |s| format!("rb show {s}")));
    Some((
        first.show.clone().unwrap_or_else(|| first.record.clone()),
        crate::assess::first_few(&shown, 4, "; "),
        shown.len(),
        look,
        vec![("records", Value::list_of_str(ids)), ("texts", Value::List(texts))],
    ))
}

/// A text an agent wrote that says who wrote it, or how.
#[derive(Debug, Clone, PartialEq)]
pub struct WriterWords {
    /// the record that holds it (a note's own id)
    pub record: String,
    /// where it is, as rb's text says it: "e1 hypothesis", "a note on e1"
    pub place: String,
    /// what rb show opens to read it, None for a note on the project
    pub show: Option<String>,
    pub marks: Vec<String>,
    /// a reason only the log keeps (rb log shows it), which no write but the next of its kind replaces
    pub from_log: bool,
}

#[cfg(test)]
mod tests {
    use super::*;

    const T: &str = "2026-05-07T20:07:00.000+00:00";

    #[test]
    fn a_command_given_later_reads_as_one() {
        let at = |made_before| command_given(Some("python eval.py"), made_before, None, Some(T)).unwrap();
        assert_eq!(at(false).line(), "command (as given): python eval.py");
        assert_eq!(at(true).line(), "command given later, not observed: python eval.py");
        assert!(command_given(Some("  "), true, None, Some(T)).is_none());
    }

    #[test]
    fn what_rb_saw_makes_it_the_run_s() {
        let journal = crate::obj! {"by" => "journal", "at" => "2026-05-07T20:00:00.000+00:00"};
        assert_eq!(command_given(Some("x"), true, Some(&journal), Some(T)).unwrap().given, "observed");
        let log = |written: &str| crate::obj! {"by" => "log", "path" => "logs/train.log", "line" => 3, "written" => written};
        let then = command_given(Some("x"), true, Some(&log("2026-05-07T20:30:00.000+00:00")), Some(T)).unwrap();
        assert_eq!((then.given, then.line()), ("logged", "command (as given): x (logs/train.log:3 records this launch)".to_string()));
        let later = command_given(Some("x"), true, Some(&log("2026-10-10T09:00:00.000+00:00")), Some(T)).unwrap();
        assert_eq!(later.given, "later");
        assert!(later.how.ends_with("; logs/train.log was written 2026-10-10, after the run"), "{}", later.how);
    }

    /// What rb saw counts only near the run: a recipe re-run today, or a file written months before, is not its launch.
    #[test]
    fn what_rb_saw_counts_only_near_the_run() {
        let journal = crate::obj! {"by" => "journal", "at" => "2026-10-11T09:00:00.000+00:00"};
        let today = command_given(Some("x"), true, Some(&journal), Some(T)).unwrap();
        assert_eq!(today.given, "later");
        assert!(today.how.ends_with("; rb's journal saw it run 2026-10-11, after the run"), "{}", today.how);
        let log = |path: &str, written: &str| crate::obj! {"by" => "log", "path" => path, "line" => 2, "written" => written};
        let old = command_given(Some("x"), true, Some(&log("logs/train.log", "2026-04-01T09:00:00.000+00:00")), Some(T)).unwrap();
        assert_eq!(old.given, "later");
        assert!(old.how.ends_with("; logs/train.log was written 2026-04-01, more than a week before the run"), "{}", old.how);
        let readme = command_given(Some("x"), true, Some(&log("README.md", "2026-05-07T20:00:00.000+00:00")), Some(T)).unwrap();
        assert_eq!(readme.given, "later");
        assert!(["README.md", "docs/run.rst", "notes/Setup.MD", "README"].iter().all(|p| is_document(p)));
        assert!(!["logs/train.log", "out/stdout.txt", "slurm-123.out"].iter().any(|p| is_document(p)));
    }

    /// rb says a run was made before it was recorded only where it knows when it was made.
    #[test]
    fn an_unknown_time_is_said_as_unknown() {
        let g = command_given(Some("python eval.py"), true, None, None).unwrap();
        assert!(g.how.starts_with("when it was made is unknown, and neither"), "{}", g.how);
        assert!(!g.how.contains("made before it was recorded"));
    }

    #[test]
    fn times_read_to_the_minute() {
        assert_eq!(minute("2026-05-07T20:07:47.000+00:00"), "2026-05-07 20:07 UTC");
        assert_eq!(minute("2026-05-07T22:07:47+02:00"), "2026-05-07 20:07 UTC");
    }

    #[test]
    fn the_record_start_line() {
        let s = |before, runs, unknown| Start { at: "2026-10-10T14:00:00.000+00:00".into(), before, runs, unknown }.words();
        let r = "; what came before it is Retrospective";
        assert_eq!(s(117, 119, 0), format!("Recorded since 2026-10-10, after 117 of its 119 runs were made{r}"));
        assert_eq!(s(4, 4, 0), format!("Recorded since 2026-10-10, after all 4 of its runs were made{r}"));
        assert_eq!(s(1, 1, 0), format!("Recorded since 2026-10-10, after its one run was made{r}"));
        assert_eq!(s(2, 2, 0), format!("Recorded since 2026-10-10, after both its runs were made{r}"));
        assert_eq!(s(3, 3, 0), format!("Recorded since 2026-10-10, after all 3 of its runs were made{r}"));
        assert_eq!(s(1, 2, 0), format!("Recorded since 2026-10-10, after 1 of its 2 runs was made{r}"));
        assert_eq!(s(2, 5, 1), format!("Recorded since 2026-10-10, after 2 of its 5 runs were made (when 1 was made is unknown){r}"));
    }
}
