This is an rb bundle: one project's record of experiments, exported whole with
rb export --with-record.

What is here

  record/.rb/     The record itself, exactly as rb keeps it: one YAML file per record.
                  project.yaml is the project; questions/, metrics/, resources/ and
                  decisions/ hold a file per question, metric, resource and decision,
                  and notes/ a file per note on the project. Each experiment is a
                  folder, experiments/<id>/, holding its experiment.yaml and a file
                  per variant under variants/, per note under notes/, per criterion
                  under criteria/, per run under runs/ and per analysis under
                  analyses/. (A record an earlier rb wrote may keep an experiment's
                  variants and notes in its experiment.yaml and all its criteria in
                  one criteria.yaml, and the project's notes in project.yaml; rb
                  still reads them as they are, and moves each into a file of its
                  own the next time it writes there.)
                  log.jsonl is an append-only log of every write with the sha256 of
                  the content written. The files either match those hashes or they
                  do not; that is the integrity check, and it needs no server and no
                  account. Every file opens with rb: 1 and its kind, and validates
                  against rb-v1.schema.json (rb schema all prints it).
  page.html       The record rendered for reading: every criterion with its comparison,
                  its rule and the rest of its reading, computed at export time. Open it
                  in any browser.
  manifest.json   Every file above with its sha256, plus what wrote this bundle and when.

What the record keeps about who wrote it

  log.jsonl and the records keep who made each write (human:<name> or
  agent:<name>), how rb knew, and the channel it came through (the CLI, the SDK
  or MCP). An agent's write also keeps its chat session and what rb knows of the
  agent: its name, version, provider and model. The user's own words an agent
  passed on with --words are kept beside the reason, and each run's receipt keeps
  the host it ran on, the machine's platform and the git branch. page.html names
  no writer, session, platform or branch, and none of those words; anyone who can
  read this bundle can read all of it.

Reading it without rb

  Everything is YAML, JSON and HTML. Start with page.html; the raw record is under
  record/.rb/. No file in this bundle requires rb.

Reopening it with rb

  pip install rabbit-brain, then run rb status inside record/. Findings are computed
  from the record on every read, never stored, so what rb shows is derived from these
  files and nothing else. If page.html and rb status ever disagree, rb status is right:
  the page is the record as computed when this bundle was written.

Files the record's checks read (a cited paper's saved text, the configs settings were
verified against) are included beside .rb/ at their own paths, so the checks resolve
here exactly as they did at home. Large artifacts (model outputs, images) are not
copied in; the record references them by path and sha256, so they can be fetched from
the code repository (named in manifest.json when known) and verified.
