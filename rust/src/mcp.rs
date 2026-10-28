//! `rb mcp`: the record over the Model Context Protocol (stdio, JSON-RPC 2.0, one message per line). Each tool is the
//! CLI command of the same name, run in this process with `--json`: the same rules, the same envelope, the same error
//! codes. Every call on a connection is recorded as `agent:<client name>` (from the client's `initialize`, or
//! `--agent`) via mcp, with the client's version and the model its agent gave the context tool. What the server says
//! of itself (its protocol versions, instructions, tools with their schemas, and resources) is declared in
//! `mcp_tools.py` and written to `assets/mcp.json` by `rust/gen/gen_mcp.py`.

use crate::cli::{eprintln_err, run_capturing_all};
use crate::pyjson::{self, py_repr, py_str, Dumps};
use crate::value::{Dict, Value};
use std::io::{BufRead, Write};
use std::sync::OnceLock;

const DATA: &str = include_str!("assets/mcp.json");

fn data() -> &'static Value {
    static D: OnceLock<Value> = OnceLock::new();
    D.get_or_init(|| pyjson::loads(DATA).expect("rust/src/assets/mcp.json is rb's own"))
}

fn text_of(key: &str) -> String {
    data().get_str(key).unwrap_or_default().to_string()
}

fn tools() -> &'static [Value] {
    data().get("tools").and_then(Value::as_list).map(|v| v.as_slice()).unwrap_or(&[])
}

fn tool(name: &str) -> Option<&'static Value> {
    tools().iter().find(|t| t.get_str("name") == Some(name))
}

/// What stops a call before it has an answer: a JSON-RPC error with its code, or what is wrong with a tool's
/// arguments, which a tool call answers as E_USAGE in the envelope.
enum Raise {
    Rpc(i64, String),
    Arguments(String),
}

/// -32602: the request is well formed and its params are not what the method takes.
fn invalid(what: &str) -> Raise {
    Raise::Rpc(-32602, format!("invalid params: {what}"))
}

/// A field of an object in the request (`of` names the object, for the error when it is not one).
fn field<'a>(object: &'a Value, of: &str, key: &str) -> Result<Option<&'a Value>, Raise> {
    match object {
        Value::Dict(d) => Ok(d.get(key)),
        _ => Err(invalid(&format!("{of} must be an object"))),
    }
}

/// `x or default`, for a value that may be absent.
fn or_else(v: Option<&Value>, default: Value) -> Value {
    match v {
        Some(x) if x.truthy() => x.clone(),
        _ => default,
    }
}

/// A tool's arguments: an object of named values, as its inputSchema says. None at all is no arguments.
fn as_arguments(name: &str, v: &Value) -> Result<Dict, Raise> {
    match v {
        Value::Dict(d) => Ok(d.clone()),
        other if !other.truthy() => Ok(Dict::new()),
        _ => Err(Raise::Arguments(format!("the arguments for {name} must be an object of named values"))),
    }
}

/// How a value is written on the command line: booleans as JSON, lists and objects as JSON, floats by repr, the rest by
/// str.
fn scalar(v: &Value) -> String {
    match v {
        Value::Bool(b) => (if *b { "true" } else { "false" }).to_string(),
        Value::List(_) | Value::Dict(_) => pyjson::dumps(v),
        other => py_str(other),
    }
}

/// An option and its value. A value that starts with '-' (-10%, -O2) is joined to its flag, --hypothesis=-10%, so the
/// parser never reads it as another option.
fn opt(flag: &str, value: String) -> Vec<String> {
    if !value.starts_with('-') {
        return vec![flag.to_string(), value];
    }
    if flag.starts_with("--") {
        vec![format!("{flag}={value}")]
    } else {
        vec![format!("{flag}{value}")]
    }
}

/// The `rb` command line a tool call stands for.
fn argv_for(name: &str, arguments: &Value) -> Result<Vec<String>, Raise> {
    let Some(t) = tool(name) else { return Err(Raise::Rpc(-32602, format!("unknown tool {}", pyjson::repr_str(name)))) };
    let arguments = as_arguments(name, arguments)?;
    let props = t.get("props").and_then(Value::as_dict).cloned().unwrap_or_default();
    let mut unknown: Vec<&String> = arguments.keys().filter(|k| !props.contains_key(*k)).collect();
    if !unknown.is_empty() {
        unknown.sort();
        unknown.dedup();
        let said = unknown.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", ");
        return Err(Raise::Arguments(format!("unknown {} for {name}: {said}", crate::assess::plural("argument", unknown.len() as i64))));
    }
    let words: Vec<String> = t.get("words").and_then(Value::as_list).map(|l| l.iter().map(py_str).collect()).unwrap_or_default();
    let mut argv = words.clone();
    let mut positional: Vec<String> = Vec::new();
    for a in t.get("args").and_then(Value::as_list).cloned().unwrap_or_default() {
        let a = py_str(&a);
        if let Some(rest) = a.strip_prefix('*') {
            match arguments.get(rest) {
                Some(Value::List(vals)) => positional.extend(vals.iter().map(py_str)),
                Some(v) if v.truthy() => positional.push(py_str(v)),
                _ => {}
            }
        } else if let Some(v) = arguments.get(&a).filter(|v| !v.is_null()) {
            positional.push(scalar(v));
        }
    }
    if name == "run_add" {
        match arguments.get("metrics") {
            Some(Value::Dict(m)) => positional.extend(m.iter().map(|(k, v)| format!("{k}={}", scalar(v)))),
            Some(v) if v.truthy() => return Err(Raise::Arguments("metrics must be an object of names and numbers".into())),
            _ => {}
        }
    }
    for (key, flag) in t.get("opts").and_then(Value::as_dict).cloned().unwrap_or_default() {
        let Some(v) = arguments.get(&key).filter(|v| !v.is_null()) else { continue };
        let switch = props.get(&key).and_then(|p| p.get("type")).and_then(Value::as_str) == Some("boolean");
        if matches!(v, Value::Bool(false)) && switch {
            continue; // a switch left off; a value of false is a value (cited=false)
        }
        let flag = py_str(&flag);
        if key == "direction" {
            argv.push(format!("--{}", py_str(v)));
        } else if matches!(v, Value::Bool(true)) && switch {
            argv.push(flag);
        } else if let Value::Dict(d) = v {
            for (k, x) in d {
                // a row's value as JSON: a comma or '=' in a text stays in it, and text stays text
                let x = if key == "where" { pyjson::dumps(x) } else { scalar(x) };
                argv.extend(opt(&flag, format!("{k}={x}")));
            }
        } else if let (Value::List(items), false) = (v, key == "keys") {
            for x in items {
                argv.extend(opt(&flag, scalar(x)));
            }
        } else if let Value::List(items) = v {
            argv.extend(opt(&flag, items.iter().map(py_str).collect::<Vec<_>>().join(",")));
        } else {
            argv.extend(opt(&flag, scalar(v)));
        }
    }
    if positional.iter().any(|x| x.starts_with('-')) {
        // after --, a value may start with '-' (-O2, -10%)
        argv.push("--".into());
        argv.extend(positional);
        return Ok(argv);
    }
    let n = words.len();
    let mut out: Vec<String> = argv[..n].to_vec();
    out.extend(positional);
    out.extend_from_slice(&argv[n..]);
    Ok(out)
}

/// The last `n` characters of a text.
fn tail(text: &str, n: usize) -> String {
    let count = text.chars().count();
    text.chars().skip(count.saturating_sub(n)).collect()
}

/// Run one tool as the command line it stands for, in this process, as the agent; returns the envelope.
fn run_tool(name: &str, arguments: &Value, agent: &str, client: &crate::ledger::Client) -> Result<Value, Raise> {
    let argv = argv_for(name, arguments)?;
    let cut = argv.iter().position(|a| a == "--").unwrap_or(argv.len());
    let mut line: Vec<String> = argv[..cut].to_vec();
    line.push("--json".into());
    line.extend_from_slice(&argv[cut..]);
    crate::ledger::set_session(Some("mcp".into()), Some(agent.to_string()));
    crate::ledger::set_client(Some(client.clone()));
    let (code, out, err) = run_capturing_all(line);
    crate::ledger::set_client(None);
    crate::ledger::set_session(None, None);
    let mut env = match pyjson::loads(&out) {
        Ok(Value::Dict(d)) => d,
        // every --json command answers with one envelope; anything else is passed on as it was said
        _ => {
            let said = if err.is_empty() { out } else { err };
            crate::dict! {"ok" => false, "errors" => Value::List(vec![crate::obj! {"code" => "E_INTERNAL", "message" => tail(&said, 2000)}])}
        }
    };
    env.insert("exit_code".into(), Value::Int(code as i64));
    Ok(Value::Dict(env))
}

fn indented(v: &Value) -> String {
    pyjson::dumps_with(v, &Dumps::indent(1)).unwrap_or_default()
}

/// The context pack's Markdown, or "" when the envelope carries none.
fn markdown_of(env: &Value) -> Value {
    match env.get("data") {
        Some(Value::Dict(d)) if !d.is_empty() => d.get("markdown").cloned().unwrap_or_else(|| Value::str("")),
        _ => Value::str(""),
    }
}

/// The lists a reconcile or status answer gives in full only when asked (`all`): at most this many of each.
const SHOWN_FOUND: usize = 100;

/// A list in `data` at `path` cut to its first SHOWN_FOUND, its length said in `cut` under `key`.
fn cut_list(data: &mut Dict, path: &[&str], key: &str, cut: &mut Dict) {
    let Some((first, rest)) = path.split_first() else { return };
    let mut at = data.get_mut(*first);
    for step in rest {
        at = at.and_then(|v| v.as_dict_mut()).and_then(|d| d.get_mut(*step));
    }
    if let Some(Value::List(items)) = at {
        if items.len() > SHOWN_FOUND {
            cut.insert(key.into(), Value::Int(items.len() as i64));
            items.truncate(SHOWN_FOUND);
        }
    }
}

/// rb reconcile's and rb status's answers as the MCP tools give them unless asked for all, so a repository of hundreds
/// of results folders answers in a size a client reads: reconcile's folders of results files alone that no run named
/// are counted and three named, as its text says them, and each long list keeps its first SHOWN_FOUND; status keeps the
/// first SHOWN_FOUND of the folders no experiment reads, in its repository and in each open item. `cut` says what was
/// left out.
fn capped(name: &str, env: Value) -> Value {
    let Value::Dict(mut env) = env else { return env };
    // cut where it stands, so the answer's keys keep their order
    let Some(Value::Dict(data)) = env.get_mut("data") else { return Value::Dict(env) };
    let mut cut = Dict::new();
    if name == "reconcile" {
        let mut loose: Vec<String> = Vec::new();
        if let Some(Value::Dict(repo)) = data.get_mut("repository") {
            if let Some(Value::List(folders)) = repo.get_mut("folders") {
                let (alone, kept): (Vec<Value>, Vec<Value>) = std::mem::take(folders).into_iter().partition(|f| f.get("unplaced").map_or(false, Value::truthy));
                loose = alone.iter().filter_map(|f| f.get("folder").map(py_str)).collect();
                *folders = kept;
            }
        }
        if !loose.is_empty() {
            if let Some(Value::Dict(read)) = data.get_mut("read") {
                read.retain(|k, _| !loose.contains(k));
            }
            cut.insert("unplaced_folders".into(), Value::Int(loose.len() as i64));
            cut.insert("first".into(), Value::list_of_str(loose.iter().take(3).cloned()));
        }
        for key in ["found", "unplaced", "uncited_changes", "recorded", "not_recorded"] {
            cut_list(data, &[key], key, &mut cut);
        }
    } else {
        cut_list(data, &["repository", "unindexed"], "unindexed", &mut cut);
        if let Some(Value::List(items)) = data.get_mut("open") {
            for it in items.iter_mut() {
                let Some(d) = it.as_dict_mut() else { continue };
                let Some(Value::List(folders)) = d.get_mut("folders") else { continue };
                if folders.len() <= SHOWN_FOUND {
                    continue;
                }
                // every long group's folders, counted together: the older work's and the new results' are two items
                let was = cut.get("open_folders").and_then(|v| v.as_i64()).unwrap_or(0);
                cut.insert("open_folders".into(), Value::Int(was + folders.len() as i64));
                folders.truncate(SHOWN_FOUND);
                // the item's subject names every folder: it names the ones kept
                let kept: Vec<String> = folders.iter().map(py_str).collect();
                d.insert("subject".into(), Value::Str(kept.join(", ")));
            }
        }
    }
    if !cut.is_empty() {
        cut.insert("all".into(), Value::Str(format!("call {name} with all: true for every one")));
        data.insert("cut".into(), Value::Dict(cut));
    }
    Value::Dict(env)
}

fn tool_result(name: &str, env: Value, arguments: &Value) -> Value {
    let ok = env.get("ok").map_or(false, Value::truthy);
    let all = arguments.get("all").map_or(false, Value::truthy);
    let env = if matches!(name, "reconcile" | "status") && ok && !all { capped(name, env) } else { env };
    let (text, env) = if name == "context" && ok {
        let md = markdown_of(&env);
        let mut d: Dict = env.as_dict().cloned().unwrap_or_default();
        // the briefing, and the actor with what rb keeps of its agent, so a model said here can be read back
        let actor = d.shift_remove("data").and_then(|data| data.get("actor").cloned());
        let mut data = crate::dict! {"markdown" => md.clone()};
        if let Some(actor) = actor {
            data.insert("actor".into(), actor);
        }
        d.insert("data".into(), Value::Dict(data));
        (md, Value::Dict(d))
    } else {
        (Value::Str(indented(&env)), env)
    };
    crate::obj! {"content" => Value::List(vec![crate::obj! {"type" => "text", "text" => text}]), "structuredContent" => env, "isError" => !ok}
}

/// A client's name as an agent's name in the record.
fn slug(text: &Value) -> Result<String, Raise> {
    let Value::Str(t) = text else { return Err(invalid("clientInfo.name must be text")) };
    let low = crate::rbyaml::py_strip(t).to_lowercase();
    let mut s = String::new();
    let mut run = false;
    for c in low.chars() {
        if c.is_ascii_alphanumeric() || "_.@+-".contains(c) {
            s.push(c);
            run = false;
        } else if !run {
            s.push('-');
            run = true;
        }
    }
    let s: String = s.trim_matches('-').chars().take(60).collect();
    Ok(if s.is_empty() { "mcp-client".into() } else { s })
}

struct Server {
    agent: Option<String>,
    /// what the client said on this connection: its version, and the model and provider its agent gave context
    client: crate::ledger::Client,
}

impl Server {
    fn agent(&self) -> String {
        self.agent.clone().filter(|a| !a.is_empty()).unwrap_or_else(|| "mcp-client".into())
    }

    /// The model and provider the agent gives the context tool hold for the rest of the connection.
    fn heard(&mut self, name: &str, arguments: &Value) {
        if name != "context" {
            return;
        }
        let said = |k: &str| arguments.get(k).and_then(Value::as_str).and_then(crate::actor::said_id);
        if let Some(m) = said("model") {
            self.client.said.model = Some(m);
        }
        if let Some(p) = said("provider") {
            self.client.said.provider = Some(p);
        }
    }

    fn handle(&mut self, msg: &Value) -> Option<Value> {
        let valid = matches!(msg, Value::Dict(_)) && msg.get_str("jsonrpc") == Some("2.0") && matches!(msg.get("method"), Some(Value::Str(_)));
        if !valid {
            let mid = if msg.is_dict() { msg.get("id").cloned().unwrap_or(Value::Null) } else { Value::Null };
            return Some(crate::obj! {"jsonrpc" => "2.0", "id" => mid, "error" => crate::obj! {"code" => -32600, "message" => "invalid request: a JSON-RPC 2.0 object with a method"}});
        }
        let method = msg.get_str("method").unwrap_or_default().to_string();
        let Some(mid) = msg.get("id").cloned() else { return None }; // a notification: nothing to answer
        let params = or_else(msg.get("params"), Value::empty_dict());
        // a fault in rb while it answers is that call's answer (-32603), and the server goes on to the next one
        let answer = match crate::cli::guarded(|| {
            // RB_TEST_MCP_FAULT=<text> stops a request outside a tool on a fault of rb's own (RB_TEST_FAULT stops a
            // tool's run, as it stops a command): the tests hold how one is said
            if method != "tools/call" {
                if let Some(text) = std::env::var("RB_TEST_MCP_FAULT").ok().filter(|t| !t.is_empty()) {
                    panic!("{text}");
                }
            }
            self.dispatch(&method, &params)
        }) {
            Ok(Ok(result)) => return Some(crate::obj! {"jsonrpc" => "2.0", "id" => mid, "result" => result}),
            Ok(Err(Raise::Rpc(code, message))) => (code, message, None),
            Ok(Err(Raise::Arguments(text))) => (-32602, format!("invalid params: {text}"), None),
            // a fault carries the report link, as a tool's answer does: its fix line, and report_url beside it
            Err(fault) => {
                let said = crate::ledger::report_link::reported(fault, "mcp");
                let url = said.extra.get("report_url").cloned().unwrap_or(Value::Null);
                (-32603, said.message, Some(crate::obj! {"fix" => said.fix, "report_url" => url}))
            }
        };
        let mut error = crate::dict! {"code" => answer.0, "message" => answer.1};
        if let Some(data) = answer.2 {
            error.insert("data".into(), data);
        }
        Some(crate::obj! {"jsonrpc" => "2.0", "id" => mid, "error" => Value::Dict(error)})
    }

    fn dispatch(&mut self, method: &str, params: &Value) -> Result<Value, Raise> {
        match method {
            "initialize" => {
                let info = or_else(field(params, "params", "clientInfo")?, Value::empty_dict());
                let client = or_else(field(&info, "clientInfo", "name")?, Value::str(""));
                if self.agent.as_deref().map_or(true, str::is_empty) {
                    self.agent = Some(if client.truthy() { slug(&client)? } else { "mcp-client".into() });
                }
                // the client's own version, as far as it is version-shaped; anything else is no version
                self.client.version = field(&info, "clientInfo", "version")?.and_then(Value::as_str).and_then(|v| crate::actor::version_part(v, false));
                let asked = field(params, "params", "protocolVersion")?.cloned();
                let protocols: Vec<String> = data().get("protocols").and_then(Value::as_list).map(|l| l.iter().map(py_str).collect()).unwrap_or_default();
                let version = match asked {
                    Some(Value::Str(a)) if protocols.contains(&a) => a,
                    _ => protocols[0].clone(),
                };
                Ok(crate::obj! {
                    "protocolVersion" => version,
                    "capabilities" => crate::obj! {"tools" => crate::obj! {"listChanged" => false}, "resources" => crate::obj! {"listChanged" => false}},
                    "serverInfo" => crate::obj! {"name" => "rabbit-brain", "version" => crate::version()},
                    "instructions" => text_of("instructions")
                })
            }
            "ping" => Ok(Value::empty_dict()),
            "tools/list" => Ok(crate::obj! {"tools" => Value::List(tools().iter().map(|t| crate::obj! {
                "name" => t.get("name").cloned().unwrap_or(Value::Null),
                "description" => t.get("description").cloned().unwrap_or(Value::Null),
                "inputSchema" => crate::obj! {
                    "type" => "object", "properties" => t.get("props").cloned().unwrap_or(Value::Null),
                    "required" => t.get("required").cloned().unwrap_or(Value::List(vec![])), "additionalProperties" => false
                }
            }).collect())}),
            "tools/call" => {
                let Some(Value::Str(name)) = field(params, "params", "name")? else { return Err(invalid("name must be the tool's name, as text")) };
                if tool(name).is_none() {
                    return Err(Raise::Rpc(-32602, format!("unknown tool {}", pyjson::repr_str(name))));
                }
                let name = name.clone();
                let arguments = field(params, "params", "arguments")?.cloned().unwrap_or(Value::Null);
                self.heard(&name, &arguments);
                let env = match run_tool(&name, &arguments, &self.agent(), &self.client) {
                    Ok(env) => env,
                    Err(Raise::Arguments(text)) => crate::obj! {"ok" => false, "errors" => Value::List(vec![crate::obj! {"code" => "E_USAGE", "message" => text}])},
                    Err(e) => return Err(e),
                };
                Ok(tool_result(&name, env, &arguments))
            }
            "resources/list" => Ok(crate::obj! {"resources" => data().get("resources").cloned().unwrap_or(Value::List(vec![]))}),
            "resources/read" => {
                let Some(uri @ Value::Str(_)) = field(params, "params", "uri")?.cloned() else { return Err(invalid("uri must be the resource's address, as text")) };
                let content = |mime: &str, text: Value| crate::obj! {"contents" => Value::List(vec![crate::obj! {"uri" => uri.clone(), "mimeType" => mime, "text" => text}])};
                match uri.as_str() {
                    Some("rb://docs") => Ok(content("text/markdown", Value::str(crate::skill::AGENTS_MD))),
                    Some("rb://start") => Ok(content("text/markdown", Value::str(crate::skill::START_MD))),
                    Some("rb://schema") => Ok(content("application/json", Value::Str(text_of("schema_text")))),
                    Some("rb://format") => Ok(content("application/schema+json", Value::Str(text_of("format_text")))),
                    Some(u @ ("rb://context" | "rb://status")) => {
                        let env = run_tool(&u[5..], &Value::empty_dict(), &self.agent(), &self.client)?;
                        let context = u == "rb://context";
                        let text = if context && env.get("ok").map_or(false, Value::truthy) { markdown_of(&env) } else { Value::Str(indented(&env)) };
                        Ok(content(if context { "text/markdown" } else { "application/json" }, text))
                    }
                    _ => Err(Raise::Rpc(-32002, format!("no resource {}", py_repr(&uri)))),
                }
            }
            other => Err(Raise::Rpc(-32601, format!("method not found: {other}"))),
        }
    }
}

/// Serve the record over stdio until the client closes it. Usage analytics, when the user enabled it, is delivered
/// in the background after a reply to a request that recorded an event, and once more for whatever is left when the
/// session ends.
pub fn serve(agent: Option<String>) -> i32 {
    let mut server = Server { agent, client: crate::ledger::Client::default() };
    eprintln_err(&format!("rb mcp {}: serving the record over stdio; every call is recorded as an agent's", crate::version()));
    let stdin = std::io::stdin();
    let mut input = stdin.lock();
    let mut raw: Vec<u8> = Vec::new();
    loop {
        raw.clear();
        match input.read_until(b'\n', &mut raw) {
            Ok(0) | Err(_) => break,
            Ok(_) => {}
        }
        // one line, ended by \n alone, as Python's stdin splits lines outside Windows (a \r\n leaves a \r, which strip takes)
        let text = String::from_utf8_lossy(&raw);
        let line = crate::rbyaml::py_strip(&text);
        if line.is_empty() {
            continue;
        }
        let reply: Option<Value> = match pyjson::loads(line) {
            Err(_) => Some(crate::obj! {"jsonrpc" => "2.0", "id" => Value::Null, "error" => crate::obj! {"code" => -32700, "message" => "parse error"}}),
            Ok(Value::List(items)) if items.is_empty() => {
                Some(crate::obj! {"jsonrpc" => "2.0", "id" => Value::Null, "error" => crate::obj! {"code" => -32600, "message" => "invalid request: an empty batch"}})
            }
            Ok(Value::List(items)) => {
                let replies: Vec<Value> = items.iter().filter_map(|m| server.handle(m)).collect();
                if replies.is_empty() {
                    None
                } else {
                    Some(Value::List(replies))
                }
            }
            Ok(msg) => server.handle(&msg),
        };
        if let Some(r) = reply {
            let mut out = std::io::stdout().lock();
            if out.write_all(format!("{}\n", pyjson::dumps(&r)).as_bytes()).and_then(|_| out.flush()).is_err() {
                break; // the client left: not an error
            }
            drop(out);
            let _ = crate::cli::guarded(crate::telemetry::flush_if_captured);
        }
    }
    let _ = crate::cli::guarded(crate::telemetry::flush_at_end);
    0
}
