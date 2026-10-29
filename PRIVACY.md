# Optional usage analytics

Rabbit Brain works with analytics disabled. Analytics is off by default: it creates no analytics identity, stores no analytics events, and makes no analytics requests until you explicitly enable it. Analytics is separate from `.rb/`, the research journal, and `rb sync`.

The purpose is to understand whether people reach a useful research record and return to use it, and to find which product operations fail. A command count is activity, not evidence that a researcher benefited.

## Controls

```sh
rb telemetry status       # inspect settings; no event upload
rb telemetry enable       # opt in, using the destination configured in this build
rb telemetry preview      # inspect the outbound batch; no event upload
rb telemetry flush        # send pending events now and wait for the answer
rb telemetry disable      # stop analytics and delete local analytics state
```

If the build has no PostHog destination, `enable` can keep events locally without sending them. To configure delivery, also supply `--project-key <public project token>` and `--host <ingestion host>`. A remote host must be an HTTPS origin without a path or embedded credentials; literal `http://127.0.0.1` with an optional port is also accepted for local capture tests. These are an event-ingestion destination, not a personal API key or account credential. You never need a PostHog account. Enabling queues one consent event for the installation and makes no network request. Repeating it with the same destination does not create another consent event.

Turning analytics on is your decision. In an agent session (Claude Code, Codex, or another agent runtime rb detects, as `rb ui` and `rb open` do), `rb telemetry enable` refuses and asks you to run it in your own terminal. Anyone can run `rb telemetry disable`.

`DO_NOT_TRACK=1` suppresses collection and transmission, including an explicit flush. So does `RB_TELEMETRY` set to any value other than `1`, `true`, `on` or `yes` (any case, surrounding spaces ignored); setting it to one of those does not opt in. CI is detected by the variables `CI`, `GITHUB_ACTIONS`, `GITLAB_CI`, `BUILDKITE`, `TF_BUILD`, `JENKINS_URL`, `TEAMCITY_VERSION`, `CODEBUILD_BUILD_ARN`, `bamboo_planKey` and `BUILDER_OUTPUT`; CI, test harness, and tutorial activity is excluded. These controls apply across the instrumented CLI, SDK, MCP, terminal UI, and local web viewer surfaces. Enabling while one of them is in effect says so, and collects nothing; the consent event is queued with the first event collected after the switch is gone.

`disable` removes the stored consent, installation identifier, destination, and queued events. Enabling again creates a new random installation identifier. Local lock files can remain. Disabling does not delete events already received by PostHog.

## What an event contains

An event has a name from a fixed list, the time it occurred, a random event UUID, and a random installation identifier reused across commands. Properties are checked against an allowlist:

| Category | Information |
|---|---|
| Version | Telemetry schema and `rb` version |
| Platform | OS and architecture categories |
| Entry point | CLI, SDK, MCP, terminal UI, or web viewer |
| Actor category | Human inferred, agent, or unknown; no name, session identifier, agent version, provider or model |
| Usage cohort | `external` for a release build, `internal` for a development or source build |
| Consent | Analytics enabled for this installation identifier |
| Operation | A normalized, recognized operation, success/failure category, and duration bucket |
| Record milestone | Project initialized, record ready, record inspected, or context delivered; readiness route is declared or reconciled |

The installation identifier identifies one local analytics configuration. It does not identify anyone, and it is not linked to a Rabbit Brain account. Several people can share an installation; one user can use several installations. No project identifier is sent, so the events do not establish whether two operations involved the same project. `human_inferred` is a runtime fallback, and can also describe an ordinary script with no recognized agent attribution.

The usage cohort is stored with the installation when analytics is enabled. It is internal for a debug build and for a build with no PostHog destination built in, such as a source or editable install, and external otherwise. `RB_TELEMETRY_COHORT` set when enabling (`internal` or `external`, any case) chooses the stored cohort instead; set later, it overrides the cohort for that one process.

## What is excluded

Analytics does not send research questions, hypotheses, metrics or their values, criteria, decisions, notes, source code, file contents, paths, project or repository names, git remotes, command arguments, raw command output, raw error text, user names, email addresses, actor names, chat-session identifiers, an agent's version, provider or model, credentials, or `.rb/` records. It does not record screens, terminal sessions, browser sessions, or keystrokes.

Reading the record through an instrumented surface can emit a usage event when analytics is enabled. That event says the surface delivered a record; it cannot establish that a human read it or that an agent relied on it.

## Local storage and delivery

Analytics state lives in `telemetry/state.json` under your Rabbit Brain configuration directory: `$RB_CONFIG_DIR` when supplied, otherwise `$XDG_CONFIG_HOME/rabbit-brain` when supplied, otherwise the platform's configuration directory with a `rabbit-brain` subdirectory. The file holds consent, the random identity, its cohort, destination, and pending events. `rb` refuses an analytics state location inside `.rb/`, including a location reached through a symlink, a different capitalization of `.rb`, or a path containing `..`, and it reads the state only from a regular file.

The queue holds at most 100 events. When it is full, operation events are dropped before milestone events, and the consent event last. Pending events older than 30 days are discarded when the queue is processed. Delivery runs in a short-lived background `rb telemetry flush`, started when it is due by a command, or by an SDK call, MCP request, terminal UI request or local web viewer request that recorded an event (polling records none). Nothing waits for it, and it keeps none of their output streams or terminals open, so it never delays them. It is attempted at most once per 60 seconds, sooner when a milestone event (consent, project initialized, record ready, record inspected, context delivered) is waiting but never twice within 5 seconds, and once more when an `rb mcp` session, `rb open`, or the terminal UI ends. While events keep arriving, that process sends them at most every 5 seconds; it exits when 5 seconds pass with nothing new, or when a send fails. Each request is bounded: 3 seconds to connect and 10 seconds in all. Remote delivery uses HTTPS; the loopback exception above is for local tests. `flush` sends immediately and waits for the answer; it respects the privacy switches. Delivery is best effort: network failures, expired events, and a full queue can leave analytics incomplete. Analytics failures do not change a research operation's result.

When a send fails, its events stay queued, within the limits above, and the next attempt waits 60 seconds, twice as long after each further failure in a row, and at most an hour, even when a milestone event is waiting. When the host refuses a send with an HTTP 4xx status other than 408 or 429, every later attempt waits the full hour until one succeeds. A successful send, or enabling a different destination, ends the wait. `flush` sends at once, whatever the wait.

Events are sent to the configured PostHog ingestion host. Every event requests no person-profile processing and no GeoIP enrichment. `rb` sends `0.0.0.0` as the event's IP property instead of a machine address. HTTPS still exposes the connection's source IP to the receiving service and network infrastructure. Operators should enable PostHog's **Discard client IP data** setting; this source tree cannot verify that account setting or promise how infrastructure logs are retained. [PostHog IP handling](https://posthog.com/tutorials/web-redact-properties#hiding-customer-ip-address)

This document describes the usage analytics payload. Explicit operations such as `rb login`, `rb sync`, or cloning a URL have their own network behavior: `sync` uploads the record's page to its chosen hub (the research bundle, with the record, only with `--with-record`), and a URL clone downloads the named bundle. At most once a day, outside CI, agent sessions and `DO_NOT_TRACK`, and until `rb config auto-update off`, a background `rb` asks PyPI's public JSON (`https://pypi.org/pypi/rabbit-brain/json`) for the newest release: a plain request that carries nothing about the user or the record, and of the machine only rb's version, in its user agent. Installed as a tool, rb then upgrades through that tool (`uv tool upgrade`, `pipx upgrade`), which downloads from PyPI as the tool always does. After an upgrade, a project already set up to sync re-syncs once to the hub it last synced to, as `rb sync` would.

## What the research record keeps about who wrote it

This is not analytics: it is what rb writes into `.rb/` itself, and it never reaches PostHog. Every write keeps who made it: the actor (`human:<name>`, named from git or the login, or `agent:<name>`), how rb knew (`actor_via`: the variable that gave an agent runtime away, an SDK or MCP session, git, the login, or `RB_ACTOR`) and the channel (`via`: the CLI, the SDK or MCP). An agent's write also keeps the chat session id its runtime exports (a human's write keeps one only when `RB_SESSION` names it) and `agent`: the agent's name and version, its provider, and the model it said it runs on. A run's or an analysis's receipt also keeps the platform string and package versions of the machine that recorded it and the git commit and branch; a run also keeps the host the caller described (`--host`).

rb's text and screens name none of it: what a command prints, the sentences rb builds, and what `rb page`, `rb export`, `rb sync`, `rb open` and `rb ui` show say what was written, never who wrote it, through what, in which session, with which agent or on which branch; the record's files, `--json`, MCP and the SDK carry it. A page (`rb page`, `rb export`, `rb sync`) also leaves out the user's words that an agent passed with `--words`, and the platform string; `rb open` and `rb ui`, the user's own screens, show them without the `your words:` tag rb's text puts on them, which would tell that an agent wrote the line. The one exception is `rb doctor`'s identity check, which says how writes from the shell it runs in are recorded: the actor and how rb knew it. All of it is in `.rb/log.jsonl` and the record's files, so it travels with the record: into git with every commit, and to its readers when you publish the record, which `rb export` and `rb sync` do only with `--with-record`; rb then says what the record keeps. Without the flag they publish the page alone. What an agent said of itself for its session is also kept in `.rb/cache/sessions/`, which stays on this machine: git ignores it and no export carries it.

## Inspecting the implementation

`rb telemetry preview` shows the currently queued outbound batch before transmission. The capture token shown there is a public project ingestion token. The allowlist and transport are implemented in the Rust telemetry module; research content is not accepted as analytics properties.

The ingestion API uses a project token rather than a personal API key. Personless events can be used for event trends and funnels without creating a named user profile. [PostHog capture API](https://posthog.com/docs/api/capture), [anonymous events](https://posthog.com/docs/data/anonymous-vs-identified-events)
