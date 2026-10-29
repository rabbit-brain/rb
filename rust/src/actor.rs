//! Who is making a write, and how rb knows: an agent runtime detected from the environment first, a chat's leftover id
//! included (RB_ACTOR=agent:<name> names it; a human: name there is ignored), else RB_ACTOR, else a human named from git
//! (user.email's local part, else user.name), else the login.

use crate::errors::{RBError, Res};
use crate::pyjson::repr_str;
use crate::value::Value;
use std::path::Path;
use std::process::Command;
use std::sync::OnceLock;

pub const ACTOR_PATTERN: &str = r"^(human|agent):[A-Za-z0-9_.@+-]{1,80}$";

#[derive(Debug, Clone, PartialEq)]
pub struct Actor {
    pub id: String,
    pub via: String,
    pub runtime: Option<String>,
    pub ignored: Option<String>,
}

impl Actor {
    pub fn is_human(&self) -> bool {
        self.id.starts_with("human:")
    }
}

fn actor_re() -> &'static regex::Regex {
    static RE: OnceLock<regex::Regex> = OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(ACTOR_PATTERN).unwrap())
}

pub fn valid_actor(s: &str) -> bool {
    actor_re().is_match(s)
}

/// The one place a writer is named where anyone reads it: rb's text, the sentences rb builds and every screen. It
/// names no one: a human's name beside an agent's missing one would give the agent away. The record, --json's
/// fields, MCP and the SDK keep who made each write.
pub fn shown_writer(_id: &str) -> Option<String> {
    None
}

/// " by <name>" after a verb, for a writer shown_writer names, else nothing: "declared{} before the evidence".
pub fn by_writer(id: &str) -> String {
    shown_writer(id).map(|name| format!(" by {name}")).unwrap_or_default()
}

/// The keys under which rb's data keeps a writer's id: a screen holds the name shown_writer gives there.
const WRITER_IDS: &[&str] = &["actor", "by", "created_by", "set_by", "declared_by", "frozen_by", "gate_by"];
/// The keys that keep how rb knew the writer, the channel, the session, the agent, an RB_ACTOR an agent session
/// ignored, and the branch or worktree a write was made on: a screen holds nothing there, whoever wrote.
const NEVER_ON_SCREEN: &[&str] = &["actor_via", "via", "session", "agent", "ignored_rb_actor", "branch", "worktree"];

/// rb's data as a screen carries it (rb open's /state.json, the views rb ui and the pages draw): every key kept, a
/// writer's id as shown_writer names it, and "" under the keys of NEVER_ON_SCREEN; with `words` false (a page anyone
/// may be sent), nothing of the user's words either. Each of those keys holds a string whatever was stored, since a
/// null where one writer has a value would tell them apart. A receipt's produced.existed.by says what an output's time
/// rests on, never who, and stays.
pub fn on_screen(v: &Value, words: bool) -> Value {
    fn walk(v: &Value, parent: &str, words: bool) -> Value {
        match v {
            Value::Dict(d) => Value::Dict(
                d.iter()
                    .map(|(k, x)| {
                        let shown = match k.as_str() {
                            "by" if parent == "existed" => x.clone(),
                            k if WRITER_IDS.contains(&k) => Value::Str(x.as_str().and_then(shown_writer).unwrap_or_default()),
                            k if NEVER_ON_SCREEN.contains(&k) => Value::str(""),
                            "words" if !words => Value::str(""),
                            _ => walk(x, k, words),
                        };
                        (k.clone(), shown)
                    })
                    .collect(),
            ),
            Value::List(l) => Value::List(l.iter().map(|x| walk(x, parent, words)).collect()),
            other => other.clone(),
        }
    }
    walk(v, "", words)
}

/// re.sub(r"[^A-Za-z0-9_.@+-]", "_", text.strip())[:80].strip("_") or "unknown"
pub fn slug(text: &str) -> String {
    let t = text.trim();
    let s: String = t
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || "_.@+-".contains(c) { c } else { '_' })
        .take(80)
        .collect();
    let s = s.trim_matches('_').to_string();
    if s.is_empty() {
        "unknown".into()
    } else {
        s
    }
}

/// What Codex sets in the commands it runs: its sandbox's variables, and the ids of its thread and session.
pub const CODEX_SIGNS: &[&str] = &["CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED", "CODEX_CI", "CODEX_THREAD_ID", "CODEX_SESSION_ID"];
/// Where a chat session is read from, first match wins. CURSOR_CONVERSATION_ID is the chat of Cursor's agent;
/// CURSOR_TRACE_ID is any Cursor terminal's, so it names a session only for a write already known as an agent's.
pub const SESSION_SIGNS: &[&str] = &[
    "RB_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CODEX_SESSION_ID",
    "CODEX_THREAD_ID",
    "CURSOR_CONVERSATION_ID",
    "CURSOR_SESSION_ID",
    "CURSOR_TRACE_ID",
    "GROK_SESSION_ID",
    "GITHUB_RUN_ID",
];

fn env(k: &str) -> Option<String> {
    std::env::var_os(k).map(|v| v.to_string_lossy().into_owned())
}

/// The chat session this write came from, read from the environment, or None.
pub fn detect_session() -> Option<String> {
    for k in SESSION_SIGNS {
        if let Some(v) = env(k) {
            let v = v.trim();
            if !v.is_empty() {
                return Some(slug(v));
            }
        }
    }
    None
}

/// The session a write by `who` is recorded with: an agent's from its runtime's variables, anyone else's only from
/// RB_SESSION, set on purpose. An editor's terminal sets a trace id for whoever types in it (CURSOR_TRACE_ID), which
/// is no chat session of a human's.
pub fn session_for(who: &Actor) -> Option<String> {
    if who.id.starts_with("agent:") {
        return detect_session();
    }
    env(SESSION_SIGNS[0]).map(|v| v.trim().to_string()).filter(|v| !v.is_empty()).map(|v| slug(&v))
}

/// (agent name, the variable that gave it away), or None.
pub fn detect_runtime() -> Option<(String, String)> {
    if env("CLAUDECODE").as_deref() == Some("1") {
        return Some(("claude-code".into(), "CLAUDECODE".into()));
    }
    if let Some(a) = env("AI_AGENT") {
        if !a.is_empty() {
            let name = a.split(|c| c == '_' || c == '/').next().unwrap_or("");
            let name = if name.is_empty() { "agent" } else { name };
            return Some((slug(name), "AI_AGENT".into()));
        }
    }
    for k in CODEX_SIGNS {
        if env(k).map_or(false, |v| !v.is_empty()) {
            return Some(("codex".into(), (*k).into()));
        }
    }
    // the commands Cursor's agent runs; a Cursor terminal a human types in has no such variable
    if env("CURSOR_AGENT").as_deref() == Some("1") {
        return Some(("cursor".into(), "CURSOR_AGENT".into()));
    }
    // a chat's leftover id or entry point, with the variable above unset: still that chat's command
    if let Some(k) = SESSION_LEFTOVERS.iter().find(|k| env(k).map_or(false, |v| !v.trim().is_empty())) {
        return Some((leftover_runtime(k).into(), (*k).into()));
    }
    if env("GITHUB_ACTIONS").as_deref() == Some("true") {
        return Some(("github-actions".into(), "GITHUB_ACTIONS".into()));
    }
    None
}

/// The runtime whose chat a leftover variable comes from.
fn leftover_runtime(var: &str) -> &'static str {
    [("CLAUDE_", "claude-code"), ("CODEX_", "codex"), ("CURSOR_", "cursor"), ("GROK_", "grok")]
        .iter()
        .find(|(prefix, _)| var.starts_with(prefix))
        .map_or("agent", |(_, name)| *name)
}

/// What an agent's runtime leaves in the commands it runs besides the variables above: its session's ids, its entry
/// point, its process and its executable. Any one of them makes a write an agent session's, and the user's own controls
/// (rb confirm, rb ui) refuse. Only variables a runtime sets in its own commands belong here: what a user exports in
/// their shell to configure a runtime (Claude Code's CLAUDE_CODE_TEAMMATE_MODE, CLAUDE_CODE_SPAWN_BACKEND,
/// CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS, CLAUDE_CONFIG_DIR) is in their own terminal too, and says no session runs.
pub const SESSION_LEFTOVERS: &[&str] = &[
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_BRIDGE_SESSION_ID",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_PID",
    // set only inside a session: its messaging channel and whether someone attends it
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CODEX_SESSION_ID",
    "CODEX_THREAD_ID",
    "CURSOR_CONVERSATION_ID",
    "CURSOR_SESSION_ID",
    "GROK_SESSION_ID",
];

/// The variable that says this is an agent session, read as every write reads one, or None: for what only the user
/// may do, where a variable an agent unset must not be enough.
pub fn agent_session_sign() -> Option<String> {
    detect_runtime().map(|(_, var)| var)
}

fn git_config(key: &str, cwd: Option<&Path>) -> Option<String> {
    let mut c = Command::new("git");
    c.args(["config", key]);
    if let Some(d) = cwd {
        c.current_dir(d);
    }
    let out = c.output().ok()?;
    let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if out.status.success() && !s.is_empty() {
        Some(s)
    } else {
        None
    }
}

fn login() -> String {
    for k in ["LOGNAME", "USER", "LNAME", "USERNAME"] {
        if let Some(v) = env(k) {
            if !v.is_empty() {
                return v;
            }
        }
    }
    // getpass.getuser() falls back to the password database
    crate::sys::account_name().unwrap_or_else(|| "unknown".into())
}

// ---------------------------------------------------------------- what rb knows of an agent

/// The provider a runtime's models usually come from: what rb records, marked inferred, when the agent says none. Every
/// other runtime's provider is only what its agent says.
pub const AGENT_PROVIDERS: &[(&str, &str)] = &[("claude-code", "anthropic"), ("codex", "openai")];
/// What Claude Code reads to run its models through another provider's API than Anthropic's (Amazon Bedrock, Google
/// Vertex AI, Microsoft Foundry, Claude Platform on AWS or on Google Cloud, Bedrock's Mantle, a cloud gateway): while
/// one is on, rb infers no provider for it.
pub const CLAUDE_CODE_ELSEWHERE: &[&str] = &[
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
    "CLAUDE_CODE_USE_MANTLE",
    "CLAUDE_CODE_USE_GATEWAY",
];

/// The provider rb infers for a runtime's models: its table's, unless the runtime runs them through another provider.
fn inferred_provider(name: &str) -> Option<&'static str> {
    // on as Claude Code reads its switches
    let on = |k: &&str| env(k).map_or(false, |v| ["1", "true", "yes", "on"].contains(&v.trim().to_lowercase().as_str()));
    if name == "claude-code" && CLAUDE_CODE_ELSEWHERE.iter().any(on) {
        return None;
    }
    AGENT_PROVIDERS.iter().find(|(k, _)| *k == name).map(|(_, p)| *p)
}

/// What an agent said of itself for this session: its model and its provider (rb context --model, --provider, or the
/// context tool).
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Said {
    pub model: Option<String>,
    pub provider: Option<String>,
}

/// A version as a runtime or a client gives it, kept as far as it is version-shaped: the digits and dots it starts with
/// (`dashes`: or dashes, which AI_AGENT writes for dots: 2-1-295 is 2.1.295). None when it starts with no digit.
pub fn version_part(raw: &str, dashes: bool) -> Option<String> {
    let s = raw.trim();
    let s = s.strip_prefix(['v', 'V']).unwrap_or(s);
    let mut parts: Vec<String> = vec![String::new()];
    for c in s.chars() {
        let last = parts.last_mut().unwrap();
        if c.is_ascii_digit() && last.len() < 9 {
            last.push(c);
        } else if (c == '.' || (dashes && c == '-')) && !last.is_empty() && parts.len() < 6 {
            parts.push(String::new());
        } else {
            break;
        }
    }
    let parts: Vec<String> = parts.into_iter().filter(|p| !p.is_empty()).collect();
    if parts.is_empty() {
        None
    } else {
        Some(parts.join("."))
    }
}

/// The longest model or provider id rb keeps, in characters.
pub const SAID_MAX: usize = 200;

/// A model's or a provider's id as an agent says it, kept verbatim but for the spaces around it. None when it is
/// empty, longer than SAID_MAX characters, or holds a control character or a line break.
pub fn said_id(text: &str) -> Option<String> {
    let s = text.trim();
    let breaks = |c: char| c.is_control() || c == '\u{2028}' || c == '\u{2029}';
    if s.is_empty() || s.chars().count() > SAID_MAX || s.chars().any(breaks) {
        None
    } else {
        Some(s.to_string())
    }
}

/// The version of the runtime `name` that AI_AGENT gives (`claude-code_2-1-295_agent`), when AI_AGENT names it.
pub fn runtime_version(name: &str) -> Option<String> {
    let a = env("AI_AGENT")?;
    let (first, rest) = a.split_once(|c| c == '_' || c == '/')?;
    if slug(if first.is_empty() { "agent" } else { first }) != name {
        return None;
    }
    version_part(rest, true)
}

/// The model this one command's agent says it runs on (RB_MODEL), kept as said_id keeps it.
pub fn env_model() -> Option<String> {
    env("RB_MODEL").and_then(|v| said_id(&v))
}

/// The provider this one command's agent says it runs on (RB_PROVIDER).
pub fn env_provider() -> Option<String> {
    env("RB_PROVIDER").and_then(|v| said_id(&v))
}

/// agent{} for a write by `who`: its name, its version (the MCP client's, else its runtime's), its provider and its
/// model, each with where rb has it from. `this_command` lets RB_PROVIDER and RB_MODEL speak for the write before what
/// the session said. None for a human's write: rb records no agent there.
pub fn agent_facts(who: &Actor, client_version: Option<&str>, said: &Said, this_command: bool) -> Option<crate::value::Dict> {
    let named = who.id.strip_prefix("agent:")?;
    let name = who.runtime.clone().filter(|r| !r.is_empty()).unwrap_or_else(|| named.to_string());
    let mut d = crate::value::Dict::new();
    d.insert("name".into(), Value::Str(name.clone()));
    if let Some(v) = client_version.and_then(|v| version_part(v, false)).or_else(|| runtime_version(&name)) {
        d.insert("version".into(), Value::Str(v));
    }
    let provider = match (this_command.then(env_provider).flatten(), said.provider.clone()) {
        (Some(p), _) => Some((p, "env")),
        (None, Some(p)) => Some((p, "said")),
        _ => inferred_provider(&name).map(|p| (p.to_string(), "inferred")),
    };
    if let Some((p, from)) = provider {
        d.insert("provider".into(), Value::Str(p));
        d.insert("provider_from".into(), Value::str(from));
    }
    let model = match (this_command.then(env_model).flatten(), said.model.clone()) {
        (Some(m), _) => Some((m, "env")),
        (None, Some(m)) => Some((m, "said")),
        _ => None,
    };
    if let Some((m, from)) = model {
        d.insert("model".into(), Value::Str(m));
        d.insert("model_from".into(), Value::str(from));
    }
    Some(d)
}

pub const RUNTIME_NAMES: &[(&str, &str)] = &[("claude-code", "Claude Code"), ("codex", "Codex"), ("cursor", "Cursor"), ("github-actions", "GitHub Actions")];

pub fn runtime_label(runtime: &str) -> String {
    let name = RUNTIME_NAMES.iter().find(|(k, _)| *k == runtime).map(|(_, v)| *v).unwrap_or(runtime);
    format!("a {name} session")
}

/// The actor of this process.
pub fn current(cwd: Option<&Path>) -> Res<Actor> {
    let runtime = detect_runtime();
    let raw = env("RB_ACTOR");
    if let Some(r) = &raw {
        if !valid_actor(r) {
            return Err(RBError::msg("E_ACTOR_INVALID", format!("RB_ACTOR is {}.", repr_str(r))));
        }
    }
    if let Some((name, var)) = &runtime {
        if let Some(r) = &raw {
            if r.starts_with("agent:") {
                return Ok(Actor { id: r.clone(), via: format!("RB_ACTOR in {}", runtime_label(name)), runtime: Some(name.clone()), ignored: None });
            }
        }
        return Ok(Actor { id: format!("agent:{name}"), via: format!("detected:{var}"), runtime: Some(name.clone()), ignored: raw });
    }
    if let Some(r) = raw {
        return Ok(Actor { id: r, via: "RB_ACTOR".into(), runtime: None, ignored: None });
    }
    if let Some(email) = git_config("user.email", cwd) {
        if email.contains('@') {
            let local = email.split('@').next().unwrap_or("");
            return Ok(Actor { id: format!("human:{}", slug(local)), via: "git-email".into(), runtime: None, ignored: None });
        }
    }
    if let Some(name) = git_config("user.name", cwd) {
        return Ok(Actor { id: format!("human:{}", slug(&name.to_lowercase().replace(' ', "-"))), via: "git-name".into(), runtime: None, ignored: None });
    }
    Ok(Actor { id: format!("human:{}", slug(&login())), via: "login".into(), runtime: None, ignored: None })
}

#[cfg(test)]
mod tests {
    use super::on_screen;
    use crate::value::Value;
    use crate::{list, obj};

    /// A screen's data keeps every key: a writer's id holds what shown_writer names (nothing today); how rb knew, the
    /// channel, the session, the agent and the branch hold nothing, at any depth; the user's words go only where they
    /// may be said; a receipt's produced.existed.by, which names no writer, stays as it is.
    #[test]
    fn a_screen_carries_no_writer() {
        let link = || obj! {
            "question" => "q1", "declared_by" => "agent:claude-code", "declared_at" => "2026-10-10T12:00:00Z", "via" => "mcp",
            "session" => "sess-1", "why" => "it bears on q1", "words" => "file it under q1",
            "retracted" => obj! {"by" => "human:ana", "at" => "2026-10-10T13:00:00Z", "why" => "moved"}
        };
        let data = obj! {
            "direct" => list![obj! {"link" => link(), "finding" => obj! {"declared" => obj! {"by" => "agent:codex", "frozen_by" => "human:ana"}}}],
            "agent" => obj! {"name" => "codex", "model" => "m"}, "actor_via" => "detected:CODEX_SANDBOX", "branch" => "codex/try",
            "produced" => obj! {"existed" => obj! {"at" => "2026-10-10T11:00:00Z", "by" => "journal"}}, "ignored_rb_actor" => Value::Null
        };
        let blank_link = obj! {
            "question" => "q1", "declared_by" => "", "declared_at" => "2026-10-10T12:00:00Z", "via" => "", "session" => "", "why" => "it bears on q1",
            "words" => "file it under q1", "retracted" => obj! {"by" => "", "at" => "2026-10-10T13:00:00Z", "why" => "moved"}
        };
        let expected = obj! {
            "direct" => list![obj! {"link" => blank_link.clone(), "finding" => obj! {"declared" => obj! {"by" => "", "frozen_by" => ""}}}],
            "agent" => "", "actor_via" => "", "branch" => "",
            "produced" => obj! {"existed" => obj! {"at" => "2026-10-10T11:00:00Z", "by" => "journal"}}, "ignored_rb_actor" => ""
        };
        assert_eq!(on_screen(&data, true), expected);
        let mut no_words = blank_link;
        if let Value::Dict(d) = &mut no_words {
            d.insert("words".into(), Value::str(""));
        }
        assert_eq!(on_screen(&data, false).get("direct").and_then(|d| d.as_list()).and_then(|l| l[0].get("link")).cloned(), Some(no_words));
    }

    /// The user's write keeps no session, no agent and no freeze where an agent's keeps them: on a screen the two read
    /// the same, value for value, so neither a value nor a null tells one writer from the other.
    #[test]
    fn the_users_write_and_an_agents_read_the_same_on_a_screen() {
        let users = obj! {
            "declared_by" => "human:ana", "via" => "cli", "session" => Value::Null, "agent" => Value::Null, "actor_via" => "git-email",
            "ignored_rb_actor" => Value::Null, "frozen_by" => Value::Null, "branch" => Value::Null, "worktree" => Value::Null, "why" => "w"
        };
        let agent = obj! {
            "declared_by" => "agent:codex", "via" => "mcp", "session" => "thr-1", "agent" => obj! {"name" => "codex"},
            "actor_via" => "detected:CODEX_SANDBOX", "ignored_rb_actor" => "human:ana", "frozen_by" => "agent:codex", "branch" => "codex/try",
            "worktree" => "wt-codex", "why" => "w"
        };
        assert_eq!(on_screen(&users, true), on_screen(&agent, true));
    }
}
