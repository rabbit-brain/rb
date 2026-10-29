# Starting the record in a project already under way

Start it now, in this turn: record where this repository's work stands, from the
files, in rb's grammar; rb computes what it means. Ask the researcher nothing.
Your memory only says where to look: the files decide. The scope is the current
frontier: the question actively worked on, with the experiments, runs, findings
and open calls around it. Older work waits and can be backfilled later.

1. Run rb context --model <your model id>. With no record here, run rb init
   "<the work's theme>". In a running session it installs nothing; keep the
   commands it gives for later sessions.
2. Read the files: rb reconcile, then rb compile with the reports, notes,
   results files and databases.
3. Infer the current frontier, never assert it: from the session's context and
   the files, or the files alone when you have no context. Uncommitted plans and
   scripts say what is on now first, then the latest activity in the store, then
   recent commits. With no context, the question is the files' own claim turned
   into a question, with every factor it names and no premise no file states
   ("calibration size matters more than bit width" asks whether it does, not
   about calibration size alone); its line goes in a note on the experiment,
   after the first rb status (step 6). Then record the runs at the frontier's
   setting that every factor of that question needs (for bit width and
   calibration size, the rows at each calibration size the files name), so a
   rule reads each factor. Where a file states the comparison for a factor the
   question names, that factor gets a rule (step 7).
4. Check each claim from memory the frontier rests on now, an earlier stage's
   included, before the first rb status (step 6): record the rule rb reads over
   only the runs the claim names, so rb computes the reading. An ordering in
   every cell is one rule per pair and metric, --at-most 0 --each (--at-least 0
   where higher is better): the researcher's words memory quotes in --words
   (step 12), memory's own claim in the statement, a file's line by --source
   <file> --quote "<its words>", never in --why. A claim's number is its rule,
   with no threshold of your own: --equals 0.755 --tolerance 0.0005, half its
   last digit (step 7); a bound ("x or less") is --at-most x, a spread ("differ
   by x or less") --equals 0 --tolerance x. The rule's statement words the claim
   it checks ("the claim that INT8 ends at 0.755 top-1 is checked here"), never
   the number as a fact. A claim check takes the default role or secondary,
   never exploratory, so a miss waits under Needs judgment. Where no rule reads
   it, a note names the file and rows (step 11). Your own query alone checks
   nothing. Backfill is for older work the frontier does not rest on, never for
   a claim it rests on.
5. Record the frontier first: its question (rb question add), its experiments,
   the rules it turns on, each linked to the question its comparison answers (rb
   criterion add ... -q <question>), then only the runs those rules read and the
   frontier's own rows (step 3). Add no narrower question to hold a comparison.
   Comparisons that share an arm share one experiment, a variant per arm, each
   rule with its own --subject and --reference: read a row once.
6. Read each run from its store: rb run add <exp> --from <file>, or one row of a
   database: --from <db> --table <table> --where <column>=<value>, with
   --produced-at as the store dates it, and --set <name>=@<column>
   (@<table>.<column> from the row another table links). A row's setting:
   --source <db>#<table>[<column>=<value>].<column>. Never write an export to
   stand in for a store or type a number from memory; --dry-run first. Set the
   shared settings before the runs (one set after reads them as under an earlier
   spec), each from a row, a config or a file at a commit before the run: a
   plan's lines set only the runs it will launch. A setting of a past run is set
   only from that run's row, its config or a file at a commit before the run;
   otherwise leave it out, with a note. A summary table written after the runs
   (a sweep's metadata table) is not a pre-run source for a run's setting. The
   rules the frontier rests on are in before the first rb status. Then run rb
   status, before any note: the standing comes first.
7. Take criteria from what is written, a document or a plan the frontier rests
   on; a statement says in plain words what the rule checks, the words it rests
   on in its quote or --words. A rule a plan sets has its file and line as the
   reason (--why "<file>:<line>"), and rb checks that the line states its
   threshold; a result a paper reports is a cited criterion (--source <file>
   --quote "..."). A file's claim is checked only where the frontier rests on
   it, by the file's own comparison, never a stronger one: a statement about
   means ("1024 images is the best of 256, 512 and 1024") is --at-least 0 per
   pair (--at-most 0 where lower is better), read on the estimate, not --better,
   which needs the interval clear of 0; its line is --source <file> --quote
   "<its words>", which rb checks at that line. Its tolerance is the rounding it
   carries: half a unit of the last digit for each rounded figure it comes from
   (-0.006 as 0.755 - 0.761 takes --tolerance 0.001); read those figures in the
   table it sits under. A threshold no document states stays unknown for the
   runs already made. A direction rule (--better) reads them too: declare one
   only where a document, or a plan the frontier rests on (an uncommitted
   script, a next step stated in this chat), states the direction for the
   conditions the rule reads, in its words, with its file and line or --words;
   never with a sentence about other conditions or another stage (one that names
   none is about the runs its figures come from). With no file stating the
   direction of the frontier's own comparison and no next step stated for when
   it holds (step 8), it takes no rule, and the report says rb does not compute
   it yet. Leave the rest to the researcher.
8. A next step the researcher stated for when the comparison holds ("if it
   holds, we ..."), earlier in this chat or in a file, is the gate of the
   experiment that holds the comparison: rb experiment gate <exp> --if-holds
   "<the action>" --words "<their words>": --if-holds is what they would do ("we
   ship INT8"), and the whole sentence goes in --words (a file's: --why
   "<file>:<line>"). No threshold, no unsaid --if-not, no next step no one
   stated; a plan's step with no such condition sets no gate. Their words give
   the direction of the comparison the gate rests on: a direction rule on it (rb
   criterion add ... --better, no threshold, those words, as the gate takes
   them), so rb computes it.
9. Give --command only for a launch someone saw run, or one a log line records
   (--command-source <file>:<line>). A command from a README, a paper or a
   script is a recipe: a note with its file and line.
10. Record no call from memory. A call the researcher asks for goes in with
    their words (rb decide <id> <action> --why "..." --words "<their words>")
    and waits for their one confirmation. A dated decision a committed file
    states is a past call on the experiment it concerns: --past --when <date>
    --source <file>:<line> --quote "<its words>" --why "<file>:<line>". It waits
    for the same one confirmation, whoever ran rb init: rb says what git shows
    of the file. A dated narrowing (an arm or a range dropped while the rest
    went on) is no park: record continue on the experiment that holds the
    dropped arms, its quote saying what was dropped, so one confirm parks
    nothing that kept running.
11. A claim no file states goes in a note that says which files were read: "no
    file I read records this: <files>". Where memory and a file differ, record
    the file and note the difference ("results.csv row 12 gives 0.761; 0.767 was
    a working figure"). Before calling memory's number different from a file's,
    check it with its own --equals rule (step 4): a file's rounding is not a
    difference. A note names no one and says no memory: no "as recalled", "as
    believed" or "remembered". Your interpretation is a note, or --when-met,
    with what it rests on; rb shows it as not confirmed.
12. Never name yourself, your model or your session in the record's text: rb
    keeps who wrote each line. Your text (a note, a --why, a statement, a gate's
    reason) calls nothing the researcher's and names no chat or session as its
    source; --words is only the researcher's words memory quotes; memory's own
    claims stay in the statement. A --why, where a file is the reason, cites the
    file and its line; a rule or gate from a next step stated in this chat takes
    its words in --words and no --why. A --why never restates a claim as fact.
    Compute no finding: read rb status.

Then report to the researcher. It ends with no question at all: what you would
ask stays open in the record (a call that waits, a rule with no threshold, a
note), and the report points at it. Never ask for a threshold, a margin,
housekeeping or a past call from memory. Say:
- what you took as the current frontier, by its question id, and why (the
  commits, files and runs it rests on), and the scope used; to change either,
  they tell the session, and older work can be backfilled later;
- where the question stands: point them at rb show <the question's id>;
- each place the files differed from what was believed;
- as findings, only what rb status computes, and plainly what it does not
  compute yet;
- the calls and experiment designs (a gate waits with its design) that wait for
  their authority: rb confirm lists each, in their own terminal, with the
  command that confirms it; and what rb status lists under Needs judgment;
  nothing no command confirms;
- when rb init installed nothing, the commands it gave for later sessions;
- when a write said rb put the code in git (refs/rb/snapshots/<tree>), where it
  is and the command that removes it;
- what you left out.
Say the researcher's words memory quotes "as you said earlier" (the gate on
<exp>, as you said earlier: "<their words>"), memory's own claims with no
speaker, a file's words with <file>:<line>; never "your words" in any form (not
"your recalled words"), and never "your rule", "in your words from earlier" or
"your call" for what rb does not list as the researcher's call. A miss is said
in rb's words ("not reproduced", "not met"), never "contradicts".
