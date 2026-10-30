//! The words the shell draws that are its own, not rb's computed text (SHELL-SPEC section 12). Everything else on a
//! shell page is the document's: rb's lines as rb computed them, and the record's author text verbatim.

/// The footer's first line on rb page and rb export: rb recognises a page it wrote by it (export.rs's PAGE_MARK).
pub const PAGE_MADE: &str = crate::export::PAGE_MARK;
/// rb open's footer, as rb open says it today: what it is, and where it lives.
pub const OPEN_MADE: &str = "recomputed from the record on every refresh";
pub const OPEN_WHERE: &str = "this page lives on 127.0.0.1 of this machine";
pub const HOW_TO_READ: &str = "How to read this record";
/// The honesty line: once, in the footer (BP 2a, `one_honesty_line`).
pub const HONEST: &str = "A decision records a call; it does not change a reading.";
pub const HOW_TO_READ_AXES: &str = "each rule is read on axes: the comparison it reads with its interval, whether the rule as declared is met, \
     when it was declared against its evidence, how many independent units it rests on, and anything wrong with what it rests on. No single word \
     stands for all of that.";
pub const HOW_TO_READ_COMPUTED: &str =
    "findings are computed, never stored, so regenerate this file after new runs. If this page and rb status ever disagree, rb status is right.";

pub const SKIP: &str = "Skip to the record";
/// About's line on the record: "Research record <id> · rb <version>" (the top bar names the record by its title alone).
pub const RESEARCH_RECORD_CAP: &str = "Research record";
pub const THEME: &str = "Theme: system";
pub const KEYBOARD: &str = "Keyboard";

pub const OVERVIEW: &str = "Overview";
pub const RESEARCH: &str = "Research";
/// One name per thing, and a nav label is its page's title (design pass 1, P9).
pub const ALL_CRITERIA: &str = "Rules";
pub const EXPERIMENTS: &str = "Experiments";
pub const RECORD: &str = "Record";
pub const RUNS: &str = "Runs";
pub const RESOURCES: &str = "Resources";
pub const HISTORY: &str = "History";
pub const YOUR_CALL: &str = "Your call";
/// The About view (design pass 1, P10): what made the page, how to read it, and the words it uses, once, reached from
/// the top bar, never at the foot of every view.
pub const ABOUT: &str = "About";
pub const ABOUT_TITLE: &str = "About this record";

/// The views' headings and ledes, rb's words as rb page has them.
pub const RESEARCH_LEDE: &str = "Each question has its own answer, evidence and open questions.";
pub const EXPERIMENTS_LEDE: &str = "Open an experiment for its comparison, settings and record.";
pub const RUNS_LEDE: &str = "Each run keeps when its output was produced and when it was recorded, the repository's state when it was recorded, and what it read and made.";
pub const HISTORY_LEDE: &str = "Each call keeps its reason and its date. A call does not change a finding.";

/// A question's view (SHELL-SPEC section 4.3).
pub const EXPERIMENTS_BEHIND: &str = "Experiments behind this question";
pub const NO_EXPERIMENT_HERE: &str = "No experiment is filed under this question.";
pub const NOTHING_WAITS_HERE: &str = "Nothing waits on you here.";
pub const NEXT_STEP: &str = "Next step";
pub const CALLS_MADE: &str = "Calls made";
/// After a criterion whose rule cites a number someone else stated.
pub const CITED: &str = "cited";
pub const NOT_IN_AN_INTERPRETATION: &str = "Rules no note speaks to";
/// A question no rule is linked to says this one sentence under its status (plain words pass 4), where rb gives no line
/// of its own for it.
pub const NO_RULE_TESTS: &str = "No rule tests this question yet.";
/// The notes whose words name a question, listed on its page (plain words pass 4); with none, nothing.
pub const NOTES_ABOUT_QUESTION: &str = "Notes about this question";
pub const WHICH_COMPARISONS: &str = "Which comparisons carry these qualifications?";
pub const BY_READING: &str = "By reading";
pub const DIRECTIONS_ALIGNED: &str = "Directions are aligned to the same comparison order.";
pub const SUB_QUESTION: &str = "Sub-question";
pub const DETAILS: &str = "details";
pub const WHEN_AND_WHY: &str = "when and why";
pub const REPRODUCE_LINK: &str = "Reproduce";
/// All criteria's filter, drawn where the script runs.
pub const FILTER_CRITERIA: &str = "Filter rules";
pub const FILTER_HINT: &str = "Filter by ID, words or state";

/// Experiments, an experiment and its Reproduce (SHELL-SPEC sections 4.5, 4.6, 4.6.1).
pub const HYPOTHESIS: &str = "hypothesis";
pub const PURPOSE: &str = "purpose";
pub const WHY: &str = "why";
pub const IF_IT_HOLDS: &str = "if it holds";
pub const IF_NOT: &str = "if not";
pub const NOT_RECORDED: &str = ": not recorded";
pub const COMPARISON: &str = "Comparison";
/// The comparison's grid (PIVOT): an empty cell, the baseline's one tag, the side grid's third column, a difference's
/// direction.
pub const NOT_RUN: &str = "Not run";
pub const BASELINE_TAG: &str = "baseline";
pub const HIGHER: &str = "higher";
pub const LOWER: &str = "lower";
pub const ALSO_DIFFERS_IN: &str = "also differs in";
pub const THE_BASELINE: &str = "the baseline";
pub const GRID_COLUMNS_APART: &str = "Each column is measured under its own settings. Compare within a column, not across.";
pub const GRID_ROWS_APART: &str = "Each row is measured under its own settings. Compare within a row, not across.";
/// "From 10 runs." under the grid.
pub fn from_runs(n: i64) -> String {
    if n == 1 { "From 1 run.".to_string() } else { format!("From {n} runs.") }
}
pub const RUN: &str = "run";
pub const ARMS: &str = "arms";
pub const RESULTS: &str = "results";
pub const COPY_RECIPE: &str = "Copy recipe";
pub const ITS_CRITERIA: &str = "Its rules";
pub const ANALYSES: &str = "Analyses";
pub const RETRACTED: &str = "retracted";
/// A retracted run's tag in a Runs table, before its receipt's link.
pub const RUN_RETRACTED: &str = "run retracted";
pub const SETTINGS: &str = "settings";
pub const NOTES: &str = "notes";
pub const WHAT_IT_TAKES: &str = "What it takes to run it again";
pub const RECIPE: &str = "Recipe, from what the record holds";
pub const WHOLE_RECIPE: &str = "The whole recipe as one script";
pub const COMMANDS_AS_RECORDED: &str = "Commands as recorded (history, not the recipe)";
pub const PER_RUN: &str = "per run";
pub const SHARED: &str = "shared";
pub const UNKNOWN: &str = "unknown";
pub const OBSERVED: &str = "observed";
pub const READS: &str = "reads";

/// "locked, then amended 3 times", over each change made after the lock.
pub fn amended_times(n: usize) -> String {
    format!("locked, then amended {n} {}", if n == 1 { "time" } else { "times" })
}

/// Runs (SHELL-SPEC section 4.7): "8 runs not retracted · 1 retracted stays on record" (plain words pass 1: not
/// retracted, which does not say counted).
pub fn runs_stand(stand: i64, retracted: i64) -> String {
    // "23 runs" (design pass 1, target 5): a retracted run is said apart, so the count needs no qualifier
    let stand = format!("{stand} {}", if stand == 1 { "run" } else { "runs" });
    if retracted == 0 {
        return stand;
    }
    format!("{stand} \u{b7} {retracted} retracted {}", if retracted == 1 { "stays on record" } else { "stay on record" })
}

/// WITNESS (Oct 28): what a run rb did not see happen says where a run it saw shows its receipt's link.
pub const RECORDED_AFTERWARDS: &str = "recorded afterwards";
/// An item's type, underneath: rb saw it happen, or recorded it afterwards.
pub const RECORDED_AS_IT_RAN: &str = "recorded as it ran";

/// A rule word as a tally says it, short and plain: "met", "not met", "can't tell yet", "not tested"; any other ("not
/// reproduced", "gate missed") as rb gives it. The screens' tallies and rb status's (cmds/reading.rs) read this one.
pub fn short_rule(word: &str) -> &str {
    match word {
        "meets" => "met",
        "misses" => "not met",
        "undetermined" => "can't tell yet",
        "untested" => "not tested",
        other => other,
    }
}

/// A record's first day as its line says it: "2026-10-28" is "28 Oct 2026"; anything else as given.
fn record_day(day: &str) -> String {
    let ymd: Vec<&str> = day.split('-').collect();
    let month = ymd.get(1).and_then(|m| m.parse::<usize>().ok()).and_then(|m| ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"].get(m.wrapping_sub(1)));
    match (month, ymd.first(), ymd.get(2)) {
        (Some(m), Some(y), Some(d)) => format!("{} {m} {y}", d.trim_start_matches('0')),
        _ => day.to_string(),
    }
}

/// The record's line where nothing was recorded afterwards and yet the record holds work from before it began, so
/// "since it began" would not be true: a clone of an address ("It began as a clone of another record."), experiments
/// that reproduce another record's, as rb clone relates each, which a page cannot tell from a relation made by hand
/// ("1 experiment reproduces an experiment of another record."), or experiments rb reconcile read from results folders
/// ("1 experiment was read from its results folder."). The first that holds is said.
pub fn witness_older_line(began: &str, clone: bool, reproduce: usize, folders: usize) -> String {
    let day = began.split(' ').next().unwrap_or("");
    let what = if clone {
        "It began as a clone of another record.".to_string()
    } else if reproduce == 1 {
        "1 experiment reproduces an experiment of another record.".to_string()
    } else if reproduce > 1 {
        format!("{reproduce} experiments reproduce experiments of another record.")
    } else if folders == 1 {
        "1 experiment was read from its results folder.".to_string()
    } else if folders > 1 {
        format!("{folders} experiments were read from their results folders.")
    } else {
        return String::new();
    };
    if day.is_empty() {
        return String::new();
    }
    format!("Kept with rb since {}. {what}", record_day(day))
}

/// The record's one line (WITNESS, Oct 28), in rb's voice, from M's facts (`home.witness`): when rb began keeping it
/// (its day, "23 Oct 2026") and what was recorded afterwards, by kind, each count with its noun ("9 runs and 1 call"),
/// naming no source: a run typed in with its past time is recorded afterwards too, and no file says it.
/// "Kept with rb since it began." where nothing was; nothing before the record's first write.
pub fn witness_line(began: &str, afterwards: &[(i64, i64, &str, &str)]) -> String {
    let day = began.split(' ').next().unwrap_or("");
    // each kind with any recorded afterwards: "1 run", "both runs", "all 9 runs", "2 runs" (of 5)
    let parts: Vec<String> = afterwards
        .iter()
        .filter(|(n, ..)| *n > 0)
        .map(|(n, total, one, many)| match (*n, *total) {
            (1, _) => format!("1 {one}"),
            (2, 2) => format!("both {many}"),
            (n, t) if n == t => format!("all {n} {many}"),
            (n, _) => format!("{n} {many}"),
        })
        .collect();
    if day.is_empty() {
        return String::new();
    }
    if parts.is_empty() {
        return "Kept with rb since it began.".into();
    }
    let day = record_day(day);
    let n: i64 = afterwards.iter().map(|(n, ..)| *n).sum();
    let listed = match parts.split_last() {
        Some((last, [])) => last.clone(),
        Some((last, rest)) => format!("{} and {last}", rest.join(", ")),
        None => String::new(),
    };
    let mut listed = listed.chars();
    let listed = listed.next().map(|c| c.to_uppercase().chain(listed).collect::<String>()).unwrap_or_default();
    // recorded after it happened, never "after <day>" (support.rs run_witness: made before it was recorded)
    let (was, it) = if n == 1 { ("was", "it") } else { ("were", "they") };
    format!("Kept with rb since {day}. {listed} {was} recorded afterwards, not as {it} happened.")
}

/// A finding's evidence line (WITNESS): how many of the runs its reading rests on were recorded afterwards.
pub fn rests_afterwards(afterwards: i64, total: i64) -> String {
    match (afterwards, total) {
        (0, _) => String::new(),
        (1, 1) => "its run recorded afterwards".into(),
        (2, 2) => "both its runs recorded afterwards".into(),
        (a, t) if a == t => format!("all {t} of its runs recorded afterwards"),
        (a, t) => format!("{a} of its {t} runs recorded afterwards"),
    }
}

/// Resources (SHELL-SPEC section 4.8).
pub const RESOURCES_LEDE: &str = "What the runs read and made, how each is identified, how to get it again, and what depends on it.";
pub const HOW_TO_GET_IT_AGAIN: &str = "how to get it again";
pub const IDENTITY: &str = "identity";
pub const WHERE: &str = "where";
pub const URL: &str = "url";
pub const READ_BY: &str = "read by";
pub const MADE_BY: &str = "made by";

/// History before its sittings: every write, newest first, as the log keeps it (SHELL-SPEC section 4.9).
pub const LOG: &str = "Raw log";

pub const WHERE_THE_WORK_STANDS: &str = "Where the work stands";
pub const COMMAND_AS_GIVEN: &str = "command (as given)";

/// How the record was made, its first part: ("All 6 rules" or "2 of 6 rules", " were written after their results came
/// in · "), the part linked apart (SHELL-SPEC section 12; plain words pass 1).
pub fn made_criteria(late: i64, criteria: i64) -> (String, &'static str) {
    // one rule of one is "The one rule", never "All 1 rules" (as the model's integrity line says it)
    let counted = match (late, criteria) {
        (1, 1) => "The one rule".to_string(),
        (l, n) if l == n => format!("All {n} rules"),
        (l, n) => format!("{l} of {n} rules"),
    };
    (counted, if late == 1 { " was written after its results came in \u{b7} " } else { " were written after their results came in \u{b7} " })
}

/// Its second part, up to "command (as given)": "no run records its " or "2 of 8 runs record their ".
pub fn made_commands(given: i64, runs: i64) -> String {
    if given == 0 {
        "no run records its ".to_string()
    } else {
        format!("{given} of {runs} runs record their ")
    }
}
/// The Overview's fallback for "Latest changes" until History's sittings exist (SHELL-SPEC section 15, `last_write`).
pub const LAST_WRITE: &str = "last write ";
/// The glossary's key for a recorded interpretation, which the screens call a note (plain words pass 3, P9).
pub const RECORDED_INTERPRETATION: &str = "note (on a question)";
pub const CONDITIONAL_INTERPRETATION: &str = "Conditional interpretation";
pub const COMPUTED_COMPARISON: &str = "Computed comparison";
pub const NO_INTERPRETATION_YET: &str = "No note on these rules yet.";
/// rb's mark on what a session wrote and the user has not confirmed (D3: such an interpretation reads as a note).
pub const NOT_CONFIRMED: &str = "not confirmed";
/// A recorded interpretation is a note (plain words pass 3): "A note, not confirmed: <its words>." until it is
/// confirmed, then "Note: <its words>.", never a reading (rb's computed number) nor a free-floating tag.
pub fn note_label(confirmed: bool) -> &'static str {
    if confirmed { "Note" } else { "A note, not confirmed" }
}
/// What a tally counts, before its words (plain words pass 3): "Rules: 4 met · 1 not reproduced".
pub const RULES_LEAD: &str = "Rules: ";
/// What an Experiments row is, before its state ("Experiment · open").
pub const EXPERIMENT_WORD: &str = "Experiment";
/// The tag on a rule that is the exception where most were written after their results (design pass 1, P6).
pub const WRITTEN_BEFORE: &str = "written before its results";
/// A question's state names its subject (plain words pass 3): "Open question", "Answered question".
pub fn question_state(word: &str) -> String {
    let w = word.trim();
    if w.is_empty() {
        return String::new();
    }
    let mut c = w.chars();
    let first: String = c.next().map(|f| f.to_uppercase().collect()).unwrap_or_default();
    let said = format!("{first}{}", c.as_str().to_lowercase());
    // a state word that already names its subject ("Open question", as the model now gives it) is said once
    if said == "Question" || said.ends_with(" question") { said } else { format!("{said} question") }
}
/// Whether a note's rules hold, as a sentence of its own (plain words pass 3), from rb's phrase (view.rs holds_said):
/// "its rule met" is "Its rule is met.", "its rule: can't tell yet" is "rb can't tell yet whether its rule is met.".
pub fn holds_sentence(holds: &str) -> String {
    let h = holds.trim().trim_end_matches('.');
    if h.is_empty() {
        return String::new();
    }
    let one = match h {
        "its rule met" => Some("Its rule is met."),
        "its rule not met" => Some("Its rule is not met."),
        "its rule: can't tell yet" => Some("rb can't tell yet whether its rule is met."),
        "its rule: not tested" => Some("Its rule has no results yet."),
        _ => None,
    };
    if let Some(s) = one {
        return s.to_string();
    }
    // rb says a rule a criterion in some lines ("its 2 criteria met"): the screens say rule
    let h = &h.replace(" criteria ", " rules ").replace(" criterion ", " rule ").replace("its criterion", "its rule");
    if h != holds.trim().trim_end_matches('.') {
        return holds_sentence(h);
    }
    if let Some(n) = h.strip_prefix("its ").and_then(|r| r.strip_suffix(" rules met")).filter(|n| n.chars().all(|c| c.is_ascii_digit())) {
        return format!("Its {n} rules are met.");
    }
    if let Some(n) = h.strip_prefix("its ").and_then(|r| r.strip_suffix(" rules not met")).filter(|n| n.chars().all(|c| c.is_ascii_digit())) {
        return format!("Its {n} rules are not met.");
    }
    let mut c = h.chars();
    format!("{}{}.", c.next().map(|f| f.to_uppercase().collect::<String>()).unwrap_or_default(), c.as_str())
}
/// The label of a relation to another experiment, and of a call made on an experiment, in the experiment's facts.
pub const RELATION: &str = "relation";
pub const DECISION: &str = "decision";
/// A note, named by what it is on, never by its id: "note on e1", "note on the project".
pub const NOTE_ON: &str = "note on ";
pub const THE_PROJECT: &str = "the project";
/// A run named where its label (its arms and per-run values) is empty: by its experiment, never by its id.
pub const RUN_OF: &str = "run of ";
pub const A_RUN: &str = "a run";
/// An analysis named where its title is empty, never by its id.
pub const AN_ANALYSIS: &str = "an analysis";
/// Before the run a Raw log row's run replaces, after the sentence that records the run replacing it.
pub const REPLACING: &str = "replacing ";
/// How a run's row says when it was made, as its receipt does.
pub const PRODUCED: &str = "produced ";
pub const RECORDED: &str = "recorded ";
/// A run table's quiet line and columns (design pass 1, target 5).
pub const RECORDED_CAP: &str = "Recorded ";
pub const PRODUCED_CAP: &str = "Produced ";
pub const EVERY_RUN: &str = "Every run: ";
/// A copy button's word.
pub const COPY: &str = "Copy";
/// A run whose production time nobody knows, in a run table.
pub const UNKNOWN_DAY: &str = "unknown";
pub const DIFFERENCE: &str = "Difference";
/// Reproduce's readiness as one plain sentence (design pass 1, target 6): "Rerun recipe: 2 of 4 parts ready", rb's own
/// line ("to run again: ✓ 2 ok · ◐ 1 partial · ○ 1 unknown") one click behind it.
pub fn rerun_ready(ok: usize, n: usize) -> String {
    if ok == n { format!("Rerun recipe: all {n} parts ready") } else { format!("Rerun recipe: {ok} of {n} parts ready") }
}
pub const AGAINST: &str = "against";
/// How what rb found is recorded, as rb page always said it.
pub const FOUND_HOW: &str = "record it from your screen, or with the command rb reconcile gives for it";
/// The same, on an export opened with no viewer named, which cannot know its reader owns the record (fix 3): said in no
/// one's voice, and only how: rb's hint before it already says it is not in the record (FIX4).
pub const FOUND_HOW_NONE: &str = "to record it: rb ui, or the command rb reconcile gives for it";
/// The top bar's words for a project cloned from a published one.
pub const A_FORK_OF: &str = "a fork of ";
/// The Rerun panel's code line: the code the runs used, at its commit, with what the copy had not committed when the
/// manifest names it (`repo.changed`), then what travels.
pub const CODE_THE_RUNS_USED: &str = "The code the runs used: ";
pub const AT_COMMIT: &str = " at commit ";
pub const WITH_CHANGES_NOT_COMMITTED: &str = ", with changes not committed: ";
pub const CODE_TRAVELS: &str = ". The record and the files its checks read travel in this bundle; rb clone gets the code at that commit and puts the record in it.";
/// The Rerun panel's way out with no script: back to the Overview (A's script draws its own Close in the dialog).
pub const CLOSE: &str = "Close";
/// The fold of a computed comparison's declarations, as rb page always titled it.
pub const DECLARATION_AND_SCOPE: &str = "Declaration and scope";
/// An experiment's varied settings, as rb show labels them.
pub const VARIES: &str = "varies";
/// A run row's link to the run's card, which holds its receipt.
pub const RECEIPT_LINK: &str = "receipt";
/// A design that waits for the user's confirmation, in rb's words (AGENTS.md: its own views say it once).
pub const DESIGN_NOT_CONFIRMED: &str = "design not confirmed";
pub const NOTE: &str = "note: ";

pub const FOUND: &str = "Found in the repository";
/// After each found item's hint, as rb open says it.
pub const FOUND_BY: &str = "found by rb watching the repository";
/// How many found items the Overview lists before "and N more".
pub const FOUND_CAP: usize = 10;

pub const WAITING: &str = "Waiting for your confirmation";
/// The same on the export, which says "you" of no call (polish 1, Oct 22 00:10).
pub const WAITING_EXPORT: &str = "Waiting for a confirmation";
/// The groups of what waits for a confirmation, as rb open names them; a kind not named here waits in the last.
pub const WAITING_GROUPS: [(&str, &str); 4] = [
    ("proposed", "Proposed, not made"),
    ("past", "Past calls mentioned, not confirmed"),
    ("gate", "Gates set, not confirmed"),
    ("design", "Experiment designs not confirmed"),
];
pub const WAITING_OTHER: &str = "Also waiting for your confirmation";
pub const WAITING_OTHER_EXPORT: &str = "Also waiting for a confirmation";
/// Before each waiting item's one command (the owner's wording, Oct 18). The export names no role (plain words pass 2):
/// it says how the item is confirmed, never who confirms it.
pub const CONFIRM_HERE: &str = "confirm in your own terminal:";
pub const CONFIRM_HERE_EXPORT: &str = "it is confirmed with rb in a terminal:";

/// The inspector, while nothing is picked on a view whose rail holds nothing (OVERVIEW-V2: its empty state, never a
/// second copy of the queue).
pub const NOTHING_PICKED: &str = "Pick a question, a rule or a run to see it here.";

/// The Overview v2 (OVERVIEW-V2.md): a projection of the record onto the calls the user makes, top to bottom.
pub const RECORDED_RUNS_N: (&str, &str) = ("recorded run", "recorded runs");
pub const CHANGED: &str = "changed ";
pub const EVIDENCE_INTEGRITY: &str = "How far to trust it";
pub const NEEDS_YOUR_JUDGMENT: &str = "Needs your judgment";
pub const NOTHING_NEEDS_JUDGMENT: &str = "Nothing waits on your judgment.";
/// The export's queue heading and its empty case: rb open's "Needs your judgment" in no one's voice, since a page that
/// leaves the machine cannot know who reads it. It counts the queue's rows; "Open calls" counts calls only (D3).
pub const NEEDS_JUDGMENT: &str = "Needs judgment";
pub const NOTHING_NEEDS_JUDGMENT_EXPORT: &str = "Nothing needs judgment.";
pub const RESEARCH_QUESTIONS: &str = "Research questions";
pub const HOW_RB_READS: &str = "How rb reads this repository";
pub const RECENT_CHANGES: &str = "Recent changes";
/// A queue row's one action, as the plan's Overview names them.
pub const REVIEW: &str = "Review";
pub const INSPECT: &str = "Inspect";
/// The queue's rows the document does not compute yet (before contract 1.6), each a verb phrase.
pub fn confirm_designs(n: usize) -> String {
    if n == 1 { "Confirm an experiment design".to_string() } else { format!("Confirm {n} experiment designs") }
}
/// What the Overview's queue rows are, counted (fix 2): "5 calls · 3 waiting for a confirmation · 2 from the
/// repository", its calls the ones the nav's Your call counts; the owner's views say "your confirmation".
pub const QN_CALL: &str = "call";
pub const QN_CALLS: &str = "calls";
pub const QN_CONFIRM: &str = "waiting for a confirmation";
pub const QN_FOUND: &str = "from the repository";
/// What waits and what rb found, each counted by what it is (fix 3), so the line agrees with the list of what waits:
/// "1 proposed call, 1 past call and 2 designs waiting for a confirmation · 1 result and 1 changed file from the
/// repository".
pub const QN_WAITS: [(&str, &str, &str); 4] =
    [("proposed", "proposed call", "proposed calls"), ("past", "past call", "past calls"), ("gate", "gate", "gates"), ("design", "design", "designs")];
pub const QN_WAITS_OTHER: (&str, &str) = ("other", "others");
pub const QN_RESULT: (&str, &str) = ("result", "results");
pub const QN_UNCITED: (&str, &str) = ("changed file", "changed files");
pub const CONFIRM_PROPOSED: &str = "Confirm a proposed call";
pub const CONFIRM_PAST: &str = "Confirm a past call";
pub const CONFIRM_GATE: &str = "Confirm a gate";
pub const CONFIRM_OTHER: &str = "Confirm what waits";
pub fn record_found(n: usize) -> String {
    if n == 1 { "Record a result rb found".to_string() } else { format!("Record {n} results rb found") }
}
pub fn inspect_uncited(n: usize) -> String {
    if n == 1 { "Inspect a changed file nothing in the record cites".to_string() } else { format!("Inspect {n} changed files nothing in the record cites") }
}
/// What the shell calls an active recorded interpretation, as the model's standing card does (M's
/// interpretation_label): the user's reading where they own it, a recorded reading where it waits for their
/// confirmation, and the recorded reading on a page that leaves the machine, which says no one's. The glossary's
/// "Recorded interpretation" stays its hint.
pub fn reading_label(confirmed: bool, away: bool) -> &'static str {
    match (confirmed, away) {
        (_, true) => "The recorded reading",
        (true, false) => "Your reading",
        (false, false) => "A recorded reading",
    }
}

/// The export's name for Your call (polish 1, Oct 22 00:10): a page that leaves the machine cannot know who reads it.
pub const OPEN_CALLS: &str = "Open calls";
pub const OPEN_CALLS_LEDE: &str = "Calls are made with rb in a terminal, never on this page.";
/// Said once, in About (plain words pass 4): no view repeats it.
pub const RB_NEVER_DECIDES: &str = "rb computes; it never decides.";
/// The queue's lead line on the export (plain words pass 4): the owner's view and a file with no viewer named, then a
/// reader's, which makes no call.
pub const QUEUE_LEDE: &str = "Open one to see why it waits and decide it.";
pub const QUEUE_LEDE_READER: &str = "Not decided yet. Open one to see why it waits.";
pub const NO_CALL_OPEN: &str = "No call is open.";
pub const SEE_OPEN_CALLS: &str = "See the open calls \u{2192}";
/// The export as the hub serves it names its viewer (OWNER-READER, Oct 25): the owner, signed in at their record's
/// address, reads a working view in rb open's words; a reader at a share link reads where the calls stand, with no
/// action. With no viewer named (an export opened from disk) the export's own words above stand.
pub const OWNER_CALLS_LEDE: &str = "Your calls are made with rb in a terminal; this page gives you the command.";
pub const READER_CALLS_LEDE: &str = "Calls are made with rb in a terminal, not on this page; it shows where they stand.";
pub const SEE_YOUR_CALLS: &str = "See your calls \u{2192}";
/// The queue's heading in the reader's view: the same rows, as status, not tasks.
pub const STILL_OPEN: &str = "Still open";
pub const READER_NOTHING_OPEN: &str = "Nothing here is still open.";
/// What a confirmation's command says in the reader's view, which shows no command: the state, in words.
pub const READER_NOT_CONFIRMED: &str = "It is not confirmed yet.";
pub const READER_DESIGN_NOT_CONFIRMED: &str = "The design is not confirmed yet.";

/// An experiment's folds (OVERVIEW-V2: settings, config values and the recipe one click down).
pub const SOURCE_DETAILS: &str = "Source details";
pub const REVIEW_THE_CALLS: &str = "Review the calls waiting on you →";
pub const CALL_IN_YOUR_TERMINAL: &str = "You make a call in your terminal; rb records it with your reason.";
pub const KEYS_LINE: &str = "<kbd>j</kbd> <kbd>k</kbd> move through the calls · <kbd>a</kbd> <kbd>r</kbd> <kbd>i</kbd> choose an action · <kbd>c</kbd> copies the command";
pub const OPEN_IN_THE_RECORD: &str = "Also open in the record";
/// An open item's state words (design pass 1, target 2).
pub const NO_RUNS_YET: &str = "no runs yet";
pub fn its_note(note: &str) -> String {
    format!("Its note: {}{}", note, if note.ends_with('.') { "" } else { "." })
}
/// "all 3 of its results came first", "2 of its 3 results came first": a rule written after its results, counted.
pub fn came_first(k: &str, n: &str) -> String {
    if k == n { format!("all {n} of its results came first") } else { format!("{k} of its {n} results came first") }
}
pub fn given_not_checked(value: &str) -> String {
    format!("given as {value}, not checked.")
}
/// The open items' group headings (design pass 1, target 2): a plain sentence with its count, then, where it helps,
/// what each item needs. None for a group the shell has no sentence for: rb's heading stands.
pub fn open_group(code: &str, n: usize) -> Option<(String, String)> {
    let (head, under) = match code {
        "untested" => ("Rules with no runs yet", ""),
        "no_interval" => ("Rules rb can't judge yet", "Each needs results from 3 or more runs for an interval."),
        "late" => ("Rules written after their results", ""),
        "provisional" => ("Settings never checked against a source", ""),
        "unindexed_folder" => ("Results folders the record does not read yet", ""),
        "setting_unknown" | "unknown_optional" => ("Settings with no value found yet", ""),
        "too_few_units" => ("Rules that need more runs", ""),
        "command_later" => ("Runs whose command was given later, not seen", ""),
        _ => return None,
    };
    Some((format!("{head} ({n})."), under.to_string()))
}
pub const NOTES_ON_THE_PROJECT: &str = "Notes on the project";
pub const REVIEW_WILL_READ: &str = "The record will say: ";

/// Reproduce this project, an Overview action on an export made with --url (rb's words, as rb page has them).
pub const REPRODUCE: &str = "Reproduce this project";
pub const REPRODUCE_LEDE: &str =
    "Everything on this page was computed from the record this bundle carries. Rerun that computation, and the runs behind it, on your machine:";
pub const REPRODUCE_AFTER: &str = "The clone carries the structure, inherited: each rule is not tested there until runs recorded on your machine read \
     reproduced or not reproduced on it, computed like any finding. What you find is yours to publish back.";

/// Rerun this research (owner-approved, Oct 26; REPRODUCE-WORDS.md): the bar's button, the panel it opens and an
/// experiment's own prompt, on an export with a clone address. One text for rb's page and the hub.
pub const RERUN: &str = "Rerun this research";
pub const RERUN_LINE: &str = "Copy this prompt into your coding agent. It clones the record, gets the code and data it names, reruns the \
     experiments, and tells you which results hold.";
pub const COPY_PROMPT: &str = "Copy prompt";
pub const COPY_PROMPT_EXPERIMENT: &str = "Copy prompt for this experiment";
pub const RERUN_BY_HAND: &str = "To do it by hand:";
pub const CODE_UNNAMED: &str = "The runs need the code; this bundle doesn't say where it is.";

/// The prompt a reader copies into their own coding agent, with the record's page, its bundle and the folder rb clone
/// makes (each as the view gives it: rb's own value, or the placeholder the hub's metas fill). `code` says whether the
/// bundle names the code (its manifest's repo, which rb export and rb sync name only where a reader can use the
/// address): step 2 then says rb clone gets it, else that the bundle holds the record alone and the code is the
/// owner's to give. With `experiment` (its
/// id, its title and its rules' ids) step 4 reruns that experiment alone and step 5 names its rules.
/// The steps are P's (out/rerun/prompt.md), checked against what rb does: rb clone gets the code into the record's
/// checkout, or says why not with the --code that gets it; rb context's "Rerun the parent" names each resource and each
/// experiment's commands, with, where a run ran other code, the command that puts that code here first (named in words,
/// so the step holds if rb's line changes); rb journal run records a command, not a run, so each run is recorded with
/// the rb run add line rb reconcile prints for it.
pub fn rerun_prompt(page: &str, bundle: &str, folder: &str, code: bool, experiment: Option<(&str, &str, &[String])>) -> String {
    let run = match experiment {
        Some((id, title, _)) => format!("Run experiment {id}, \u{201c}{}\u{201d}: its commands", typographic(title)),
        None => "Run each experiment's commands".to_string(),
    };
    let tell = match experiment {
        Some((_, _, rules)) if rules.len() == 1 => format!("whether its rule {} reproduced, with its numbers", rules[0]),
        Some((_, _, rules)) if !rules.is_empty() => format!("which of its rules ({}) reproduced and which did not, with their numbers", rules.join(", ")),
        _ => "which rules reproduced and which did not, with their numbers".to_string(),
    };
    let clone = if code {
        format!(
            "rb clone {bundle} {folder}. This bundle names the code: rb clone gets it too, at the commit the record names, \
             and puts the record in it. If it says it could not get the code, tell me what it says and stop: once I give \
             you an address of the code, rb clone {bundle} <another new folder> --code <that address> gets it."
        )
    } else {
        format!(
            "rb clone {bundle} {folder}. This bundle holds the record alone: the code is its owner's to give, and the \
             runs need it. Tell me so and stop: once I give you an address of the code, rb clone {bundle} <another new \
             folder> --code <that address> gets it at the commit the record names and puts the record in it."
        )
    };
    format!(
        "Rerun this research with Rabbit Brain (rb). Its record: {page}\n\n\
         1. Install rb: pip install rabbit-brain\n\
         2. Clone the record into a new folder: {clone}\n\
         3. In that folder, run rb context and read it before anything else. Its \"Rerun the parent\" part names the code \
         and whether it is here, each resource, such as a model or a dataset, with how to get it, and the first command \
         for each experiment.\n\
         4. Get those resources. {run} in the order rb context names them, each as rb journal run -- <command>; where rb \
         context names a command that puts a run's code here, run that one first. After each, run rb reconcile and record \
         the run it proposes with the rb run add line it prints.\n\
         5. Then run rb status, and tell me {tell}. Do not change a rule. If something cannot run here, say what and why."
    )
}

/// A title as the prompt quotes it: its own straight quotes made typographic, so the quotes rb puts around it stay
/// the only ones that open and close it (A's second review).
fn typographic(title: &str) -> String {
    let mut inside = false;
    title
        .chars()
        .map(|c| match c {
            '"' => {
                inside = !inside;
                if inside { '\u{201c}' } else { '\u{201d}' }
            }
            '\'' => '\u{2019}',
            c => c,
        })
        .collect()
}

/// "N results to review", a question's calls on its card and in the inbox.
pub fn results_to_review(n: usize) -> String {
    format!("{n} {}", if n == 1 { "result to review" } else { "results to review" })
}

/// "and N more", after a capped list.
pub fn and_more(n: usize) -> String {
    format!("and {n} more")
}

/// The glossary (SHELL-SPEC sections 7.5 and 12, row 32): each term the page draws with a hint, with rb's own line for
/// it (rb context's Words, rb page's, AGENTS.md's, the freeze help). The fold "What the words on this page mean" holds
/// the same lines, so the document has them with no script.
pub const HINTS: [(&str, &str); 40] = [
    ("open (question)", "no call has closed it. A call on the question closes it: answered (accept), dropped (reject), parked, stopped, pivoted or shipped; investigate and continue keep it open. A finding or an interpretation does not make that call."),
    ("note (on a question)", "recorded words on what meeting a rule would mean for the question. A note counts only while its rule is met; rb computes the finding and never asserts the inverse."),
    ("Computed comparison", "rb's reading of a declared reversal between two rules: both numbers and intervals stay visible, and it makes no call on the question."),
    ("headline", "rb's one line on a question's evidence, the first that applies: No rule tests this question yet (rules are filed here but none is linked to it), Evidence needs review (an integrity problem), No evidence recorded yet, Measured differences; uncertainty remains (an interval includes 0), Evidence includes point estimates only (a reading has no interval), else Evidence available for review."),
    ("rule written before the results", "the rule was declared before the evidence it reads existed: a test planned in advance."),
    ("rule written after the results", "the rule or its question link was declared after the evidence existed or after the data was seen: never read as a test planned in advance."),
    ("no interval", "one estimate with no interval, so how much it could vary is unknown. rb gives one from 3 units, and none across fixed design cells."),
    ("Uncertainty remains", "unresolved: the interval includes 0."),
    ("Fixed design cells", "the reading spans fixed design choices: each cell is read on its own, with how many meet and their range, and no sampling interval across cells."),
    ("Reversal observed; repeatability uncertain", "the two sides of a declared reversal measured opposite directions, but not both with intervals clear of 0."),
    ("rb's interval", "95%, from 3 units: a Student t interval when the arms pair up by their unit, Welch (unpaired) when they never share one."),
    ("straddles", "the interval crosses the rule's threshold: the rule word holds on the estimate, not across the whole interval."),
    ("rule", "a threshold or comparison declared for a question; commands call it a criterion."),
    ("can't tell yet", "there are results, but they don't settle the rule: it needs an interval, more units, or the metric's direction."),
    ("result", "gain or loss (the interval clear of 0, read in the metric's direction), unresolved (the interval includes 0), equivalent (inside a declared margin), no interval (one estimate, no interval yet), measured (a level), higher or lower (the metric has no direction), not tested."),
    ("timing", "declared before the evidence (before its runs, when only its baseline's runs came first: timing is read on the subject's runs); after the evidence existed; after the data was seen; order unknown."),
    ("units", "the independent units a reading rests on. A repeat checks a run and adds no unit; a reanalysis adds none."),
    // the cards say "checks: ok" (plain words pass 1); "integrity" is the key rb's JSON and older callers use (ALIASES)
    ("checks", "problems with what a finding depends on (a stale source, an undeclared difference, a record edited outside rb). ok when there are none. Not the repository's own checks."),
    ("estimate meets the rule", "met on the estimate, not across the whole interval; never drawn green."),
    ("verified", "the named file, run or database row states this setting's value. It does not mean a run used it. provisional: given, not checked."),
    ("open (experiment)", "planned: an experiment nothing tests yet. open: evidence and no call. accepted, rejected, investigating, parked, stopped, pivoted, shipped: what the latest call on it made it."),
    ("locked", "the spec and rules are locked; a later change is recorded as an amendment."),
    ("not locked", "rules and settings can still change; a freeze locks them."),
    // a reading's result, as rb page always said it on the word (AGENTS.md: "a level with an interval", "untested")
    ("not tested", "no results for this rule yet: no recorded run reads this comparison"),
    ("measured", "one arm on its own, with its interval, so neither better nor worse"),
    ("command (as given)", "--command records what produced the numbers. rb did not run it."),
    // a tag on what waits for the user's confirmation (OVERVIEW-V2: ontology in tags), naming no writer
    ("not confirmed", "it waits to be confirmed with rb in a terminal; until then it reads as a note, never as a call made or an answer."),
    ("reconstructible", "present, changed, remote, reconstructible or lost: reconstructible means no copy is recorded, but how to get it again is."),
    // the kept terms of art the cards draw (plain words pass 2: every one has a one-line hover)
    ("gain", "the interval is clear of 0 on the better side."),
    ("loss", "the interval is clear of 0 on the worse side."),
    ("unresolved", "the interval includes 0, which is not evidence of no difference."),
    ("equivalent", "the interval lies inside the margin an equivalence rule declares."),
    ("reproduced", "a rule from a cited source or from the record this one was cloned from, met by the runs recorded here."),
    ("not reproduced", "a rule from a cited source or from the record this one was cloned from, not met by the runs recorded here."),
    ("paired difference", "each unit's subject minus its reference, averaged over the units."),
    ("cited", "a number someone else stated (a paper's table, say), checked against the source's own words."),
    ("margin", "how far the value the rule judges (the estimate, an interval bound or the worst unit) sits from its threshold: positive inside it, negative outside. Not a declared equivalence or noninferiority margin."),
    ("question link", "the question a rule's finding bears on, with the recorded words on what meeting the rule would mean for it."),
    ("identity", "how exactly a resource is named: exact (the hash of its bytes), version (a release) or name only."),
    ("amended", "changed after the rules and settings were locked; the change, its reason and what it changed from are kept."),
];

/// The glossary fold's summary, and the anchor of each line: `g-` and the term in lower case, each run of other
/// characters one hyphen ("g-open-question", "g-rb-s-interval").
pub const WORDS: &str = "What the words on this page mean";

pub fn glossary_id(term: &str) -> String {
    let term = canonical(term);
    let mut out = String::from("g-");
    let mut gap = false;
    for c in term.chars().flat_map(char::to_lowercase) {
        if c.is_ascii_alphanumeric() {
            if gap && out.len() > 2 {
                out.push('-');
            }
            gap = false;
            out.push(c);
        } else {
            gap = true;
        }
    }
    out
}

/// rb's rule words as the shell's screens say them (plain words pass 1): the display noun is "rule" ("3 rules met",
/// "rule not met"), undetermined reads "can't tell yet", untested "not tested", a reading's point only "no interval",
/// the tags Retrospective and No interval "rule written after the results" and "no interval" (each the glossary's key).
/// Commands, keys and rb's own phrase (which keys colours and anchors) are unchanged; this is display text only. A
/// leading count is kept; any other phrase ("not reproduced", "gate missed") is as rb gives it.
pub fn plain_word(phrase: &str) -> String {
    let (n, rest) = match phrase.split_once(' ') {
        Some((n, rest)) if !n.is_empty() && n.chars().all(|c| c.is_ascii_digit()) => (Some(n), rest),
        _ => (None, phrase),
    };
    let said = match rest {
        "criteria met" | "criterion met" => if n == Some("1") || n.is_none() { "rule met" } else { "rules met" },
        "criteria not met" | "criterion not met" => if n == Some("1") || n.is_none() { "rule not met" } else { "rules not met" },
        "criteria undetermined" | "criterion undetermined" | "undetermined" => "can't tell yet",
        "criteria untested" | "criterion untested" | "untested" => "not tested",
        "gate undetermined" => "gate, can't tell yet",
        "gate untested" => "gate not tested",
        "estimate meets criterion" => "estimate meets the rule",
        "recorded values meet criterion" => "recorded values meet the rule",
        "point only" | "No interval" => "no interval",
        "Retrospective" | "retrospective" => "rule written after the results",
        other => other,
    };
    match n {
        Some(n) => format!("{n} {said}"),
        None => said.to_string(),
    }
}

/// rb's line for a term the page draws, if it has one.
/// Other keys a caller may ask for, each with the glossary term it means: the rule chip asks for the noun ("rule
/// (noun)"), and a finding's checks are rb's "integrity". Each resolves to one glossary line, so the fold holds no term
/// twice and every hover points at a line that is there.
pub const ALIASES: [(&str, &str); 2] = [("rule (noun)", "rule"), ("integrity", "checks")];

fn canonical(term: &str) -> &str {
    ALIASES.iter().find(|(a, _)| *a == term).map_or(term, |(_, t)| *t)
}

pub fn hint(term: &str) -> Option<&'static str> {
    let term = canonical(term);
    HINTS.iter().find(|(t, _)| *t == term).map(|(_, line)| *line)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// WITNESS: the record's one line, in rb's voice: its day and what was recorded afterwards by kind, singular where
    /// one; "since it began" where nothing was; nothing before a first write. A finding's count says all or both.
    #[test]
    fn the_record_line_says_what_was_recorded_afterwards() {
        let none = [(0, 3, "run", "runs"), (0, 1, "call", "calls")];
        assert_eq!(witness_line("2026-10-23 22:57 UTC", &none), "Kept with rb since it began.");
        assert_eq!(witness_line("", &none), "");
        let day = "2026-10-28 09:00 UTC";
        assert_eq!(witness_older_line(day, true, 1, 2), "Kept with rb since 28 Oct 2026. It began as a clone of another record.");
        assert_eq!(witness_older_line(day, false, 1, 2), "Kept with rb since 28 Oct 2026. 1 experiment reproduces an experiment of another record.");
        assert_eq!(witness_older_line(day, false, 2, 0), "Kept with rb since 28 Oct 2026. 2 experiments reproduce experiments of another record.");
        assert_eq!(witness_older_line(day, false, 0, 1), "Kept with rb since 28 Oct 2026. 1 experiment was read from its results folder.");
        assert_eq!(witness_older_line(day, false, 0, 3), "Kept with rb since 28 Oct 2026. 3 experiments were read from their results folders.");
        assert_eq!(witness_older_line(day, false, 0, 0), "");
        assert_eq!(witness_older_line("", true, 0, 0), "");
    }

    /// 0.6.2: one table of a tally's short rule words, public, so rb status's tally reads it rather than a copy.
    #[test]
    fn a_tally_says_each_rule_word_short() {
        let said: Vec<&str> = ["meets", "misses", "undetermined", "untested", "not reproduced"].iter().map(|w| short_rule(w)).collect();
        assert_eq!(said, ["met", "not met", "can't tell yet", "not tested", "not reproduced"]);
        assert_eq!(
            witness_line("2026-10-28 22:57 UTC", &[(2, 8, "run", "runs"), (0, 1, "analysis", "analyses"), (1, 4, "call", "calls")]),
            "Kept with rb since 28 Oct 2026. 2 runs and 1 call were recorded afterwards, not as they happened."
        );
        assert_eq!(witness_line("2026-05-07 09:00 UTC", &[(1, 3, "run", "runs")]), "Kept with rb since 7 May 2026. 1 run was recorded afterwards, not as it happened.");
        assert_eq!(witness_line("2026-05-07 09:00 UTC", &[(1, 1, "run", "runs")]), "Kept with rb since 7 May 2026. 1 run was recorded afterwards, not as it happened.");
        assert_eq!(
            witness_line("2026-10-23 22:57 UTC", &[(9, 9, "run", "runs"), (0, 0, "analysis", "analyses"), (2, 5, "call", "calls")]),
            "Kept with rb since 23 Oct 2026. All 9 runs and 2 calls were recorded afterwards, not as they happened."
        );
        assert_eq!(
            witness_line("2026-10-23 22:57 UTC", &[(2, 2, "run", "runs"), (1, 2, "analysis", "analyses"), (1, 1, "call", "calls")]),
            "Kept with rb since 23 Oct 2026. Both runs, 1 analysis and 1 call were recorded afterwards, not as they happened."
        );
        assert_eq!(rests_afterwards(0, 4), "");
        assert_eq!(rests_afterwards(1, 1), "its run recorded afterwards");
        assert_eq!(rests_afterwards(2, 2), "both its runs recorded afterwards");
        assert_eq!(rests_afterwards(6, 6), "all 6 of its runs recorded afterwards");
        assert_eq!(rests_afterwards(3, 6), "3 of its 6 runs recorded afterwards");
    }

    /// Plain words pass 3: a question's state names its subject, and a note's rules say their state as a sentence.
    #[test]
    fn a_state_names_its_subject() {
        assert_eq!(question_state("OPEN"), "Open question");
        assert_eq!(question_state("answered"), "Answered question");
        // plain words pass 4: a state word that already names its subject is said once ("Open question question")
        assert_eq!(question_state("Open question"), "Open question");
        assert_eq!(question_state("open QUESTION"), "Open question");
        assert_eq!(made_criteria(1, 1), ("The one rule".to_string(), " was written after its results came in \u{b7} "));
        assert_eq!(made_criteria(6, 6).0, "All 6 rules");
        assert_eq!(made_criteria(1, 6), ("1 of 6 rules".to_string(), " was written after its results came in \u{b7} "));
        assert_eq!(holds_sentence("its rule met"), "Its rule is met.");
        assert_eq!(holds_sentence("its 2 criteria met"), "Its 2 rules are met.");
        assert_eq!(holds_sentence("its rule: can't tell yet"), "rb can't tell yet whether its rule is met.");
        assert_eq!(holds_sentence("its criterion not met"), "Its rule is not met.");
        assert_eq!(note_label(false), "A note, not confirmed");
        assert_eq!(rerun_ready(4, 4), "Rerun recipe: all 4 parts ready");
        assert_eq!(open_group("no_interval", 2).unwrap().0, "Rules rb can't judge yet (2).");
    }

    /// Plain words pass 1: rb's rule phrases as the screens say them, a count kept, anything else as rb gives it.
    #[test]
    fn rbs_rule_words_read_plainly() {
        for (rb, shown) in [
            ("criterion met", "rule met"),
            ("3 criteria met", "3 rules met"),
            ("1 criterion not met", "1 rule not met"),
            ("2 criteria undetermined", "2 can't tell yet"),
            ("criterion untested", "not tested"),
            ("estimate meets criterion", "estimate meets the rule"),
            ("point only", "no interval"),
            ("Retrospective", "rule written after the results"),
            ("not reproduced", "not reproduced"),
            ("gate missed", "gate missed"),
            ("gate undetermined", "gate, can't tell yet"),
            ("1 gate untested", "1 gate not tested"),
        ] {
            assert_eq!(plain_word(rb), shown, "{rb}");
        }
        // every plain word a tag or a tally draws has its glossary line
        for key in ["can't tell yet", "not tested", "no interval", "rule written after the results", "estimate meets the rule", "rule"] {
            assert!(hint(key).is_some(), "{key}");
        }
    }

    /// Plain words pass 2: every term the cards and views ask the glossary for has its line and an anchor the fold
    /// holds, the rule chip's noun and the cards' "checks" included; the fold holds no term twice; and no line names a
    /// role ("the record's owner"): it says how a call is made, never who makes it.
    #[test]
    fn every_term_asked_for_has_one_line_and_no_role_word() {
        let src = [include_str!("nodes.rs"), include_str!("views.rs")].concat();
        let asked: Vec<&str> = ["term(\"", "term_as(\""]
            .iter()
            .flat_map(|call| src.split(call).skip(1))
            .filter_map(|rest| rest.split('"').next())
            .collect();
        for key in asked.iter().copied().chain(["rule (noun)", "integrity", "checks", "rule", "gain", "loss", "unresolved", "not reproduced", "paired difference", "amended"]) {
            let line = hint(key).unwrap_or_else(|| panic!("no glossary line for {key:?}"));
            assert!(!line.contains('\n') && !line.contains('\u{2014}'), "{key}");
            assert!(HINTS.iter().any(|(t, _)| glossary_id(t) == glossary_id(key)), "{key} points at no line");
        }
        assert_eq!(glossary_id("rule (noun)"), "g-rule");
        assert_eq!(glossary_id("integrity"), "g-checks");
        // a card's margin is the estimate's distance to the threshold (assess.rs threshold()), and reproduced is said
        // of an inherited rule as of a cited one (assess.rs standing words)
        // (the estimate, or for a --bound rule an interval bound, or for --each the worst unit: assess.rs)
        assert!(hint("margin").unwrap().starts_with("how far the value the rule judges (the estimate, an interval bound or the worst unit) sits from its threshold"));
        for word in ["reproduced", "not reproduced"] {
            assert!(hint(word).unwrap().contains("cited source or from the record this one was cloned from"), "{word}");
        }
        assert_eq!(hint("rule (noun)"), Some("a threshold or comparison declared for a question; commands call it a criterion."));
        let mut ids: Vec<String> = HINTS.iter().map(|(t, _)| glossary_id(t)).collect();
        ids.sort();
        ids.dedup();
        assert_eq!(ids.len(), HINTS.len());
        for line in HINTS.iter().map(|(_, l)| *l).chain([CONFIRM_HERE, CONFIRM_HERE_EXPORT, OPEN_CALLS_LEDE, OWNER_CALLS_LEDE, READER_CALLS_LEDE, STILL_OPEN, READER_NOTHING_OPEN, READER_NOT_CONFIRMED, READER_DESIGN_NOT_CONFIRMED]) {
            assert!(!line.contains("owner"), "{line}");
        }
    }
}

/// The words a metric's name holds that prose writes in capitals: C's list (nodes.rs, FIX5), one list for every view's
/// title of a metric.
pub const ACRONYMS: &[&str] = &[
    "onnx", "gpu", "cpu", "tpu", "auc", "roc", "mse", "rmse", "mae", "iou", "bleu", "rouge", "wer", "cer", "ece", "fid", "psnr", "ssim", "nll", "kl", "ema",
    "mfu", "flops", "f1", "ndcg", "mrr", "llm", "ttft", "qps", "rps",
];
