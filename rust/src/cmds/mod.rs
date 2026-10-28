//! The record's commands: argument parsing helpers and what each command says. The rules live in the ledger; this
//! parses arguments and says what happened.

pub mod info;
pub mod reading;
pub mod share;
pub mod tour;
pub mod upkeep;
pub mod record;

use crate::argparse::Namespace;
use crate::cli::Out;
use crate::errors::{RBError, Res};
use crate::ledger::{show, Ledger};
use crate::pyjson::{self, repr_str};
use crate::record::*;
use crate::sources;
use crate::value::{Dict, Value};
use std::path::{Path, PathBuf};

pub const RELATIONS: &[&str] = &["reproduces", "extends", "follows_from", "compares_with"];
pub const RESOURCE_TYPES: &[&str] = &["dataset", "checkpoint", "code", "environment", "artifact", "other"];
pub const PROVIDERS: &[&str] = &["local", "url", "git", "wandb", "mlflow", "s3", "gcs", "hf", "runpod", "other"];
pub const STATUS_CAVEATS: usize = 3;
pub const TALLY_ORDER: &[&str] = &["meets", "reproduced", "misses", "not reproduced", "undetermined", "untested", "gate met", "gate missed", "gate undetermined", "gate untested"];

// ---------------------------------------------------------------- namespace access

pub fn arg<'a>(ns: &'a Namespace, k: &str) -> &'a Value {
    static NULL: Value = Value::Null;
    ns.get(k).unwrap_or(&NULL)
}

pub fn s(ns: &Namespace, k: &str) -> Option<String> {
    match ns.get(k) {
        Some(Value::Str(x)) => Some(x.clone()),
        _ => None,
    }
}

/// args.x or "": a string argument, empty when it was not given (or given empty).
pub fn s_or(ns: &Namespace, k: &str) -> String {
    s(ns, k).unwrap_or_default()
}

pub fn flag(ns: &Namespace, k: &str) -> bool {
    ns.get(k).map_or(false, |v| v.truthy())
}

pub fn f(ns: &Namespace, k: &str) -> Option<f64> {
    match ns.get(k) {
        Some(Value::Float(x)) => Some(*x),
        Some(Value::Int(i)) => Some(*i as f64),
        _ => None,
    }
}

pub fn list(ns: &Namespace, k: &str) -> Vec<String> {
    match ns.get(k) {
        Some(Value::List(l)) => l.iter().map(|v| v.as_str().map(|x| x.to_string()).unwrap_or_else(|| pyjson::py_str(v))).collect(),
        _ => Vec::new(),
    }
}

/// [p.strip() for item in items for p in item.split(",") if p.strip()]
pub fn comma_list(items: &[String]) -> Vec<String> {
    items.iter().flat_map(|item| item.split(',').map(|p| strip(p).to_string()).filter(|p| !p.is_empty()).collect::<Vec<_>>()).collect()
}

pub fn strip(s: &str) -> &str {
    crate::rbyaml::py_strip(s)
}

pub fn trunc(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// f"{x:g}"
pub fn g(x: f64) -> String {
    crate::pyfloat::fmt_float(x, "g")
}

pub fn fmt(x: f64, spec: &str) -> String {
    crate::pyfloat::fmt_float(x, spec)
}

// ---------------------------------------------------------------- paths, as pathlib writes them

/// str(Path(text)): slashes collapsed, "." parts dropped, no trailing slash. On Windows a path typed with `\` is the
/// same path with `/`, and a drive keeps its root (`C:\` is `C:/`).
pub fn pathlib_str(text: &str) -> String {
    let text = &crate::sys::typed(text);
    if text.is_empty() {
        return ".".into();
    }
    let absolute = text.starts_with('/');
    let double = text.starts_with("//") && !text.starts_with("///");
    let parts: Vec<&str> = text.split('/').filter(|p| !p.is_empty() && *p != ".").collect();
    let body = parts.join("/");
    let drive = cfg!(windows) && parts.len() == 1 && body.len() == 2 && body.ends_with(':') && text.len() > 2;
    if drive {
        return format!("{body}/");
    }
    if double {
        format!("//{body}")
    } else if absolute {
        format!("/{body}")
    } else if body.is_empty() {
        ".".into()
    } else {
        body
    }
}

/// os.path.normpath for an absolute path.
fn normpath(p: &Path) -> PathBuf {
    let mut out: Vec<String> = Vec::new();
    for c in p.components() {
        match c {
            std::path::Component::RootDir => out.clear(),
            std::path::Component::CurDir => {}
            std::path::Component::ParentDir => {
                out.pop();
            }
            std::path::Component::Normal(x) => out.push(x.to_string_lossy().into_owned()),
            std::path::Component::Prefix(_) => {}
        }
    }
    PathBuf::from(format!("/{}", out.join("/")))
}

/// os.path.relpath(path, start) for absolute paths.
pub fn relpath(path: &Path, start: &Path) -> String {
    let a: Vec<String> = normpath(path).components().skip(1).map(|c| c.as_os_str().to_string_lossy().into_owned()).collect();
    let b: Vec<String> = normpath(start).components().skip(1).map(|c| c.as_os_str().to_string_lossy().into_owned()).collect();
    let common = a.iter().zip(&b).take_while(|(x, y)| x == y).count();
    let mut parts: Vec<String> = vec!["..".to_string(); b.len() - common];
    parts.extend(a[common..].iter().cloned());
    if parts.is_empty() {
        ".".into()
    } else {
        parts.join("/")
    }
}

/// Path.cwd() / p, as pathlib joins: the working directory and the path's own parts.
pub fn absolute(p: &str) -> PathBuf {
    let path = PathBuf::from(pathlib_str(p));
    if path.is_absolute() {
        path
    } else {
        let cwd = std::env::current_dir().unwrap_or_default();
        if pathlib_str(p) == "." {
            cwd
        } else {
            cwd.join(path)
        }
    }
}

/// A path as typed becomes relative to the directory holding .rb/, keeping the last component as named.
pub fn root_relative(path: &str, root: &Path) -> String {
    let abs = absolute(path);
    let name = abs.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    let parent = abs.parent().map(|p| p.to_path_buf()).unwrap_or_else(|| PathBuf::from("/"));
    let full = sources::resolve_path(&parent).join(&name);
    let full = if name.is_empty() { sources::resolve_path(&parent) } else { full };
    if let Ok(rel) = full.strip_prefix(root) {
        let s = rel.to_string_lossy().replace('\\', "/");
        return if s.is_empty() { ".".into() } else { s };
    }
    if let Some(top) = sources::git_top(root) {
        if full == top || full.starts_with(&top) {
            return relpath(&full, root);
        }
    }
    crate::sys::slashed(&full)
}

// ---------------------------------------------------------------- values and sources from the command line

/// A setting's value from the command line: JSON when it parses as a number, boolean, quoted string or list of those.
pub fn parse_value(text: &str) -> Res<Value> {
    if strip(text).is_empty() {
        return Err(RBError::msg("E_OBJECT_INVALID", "An empty value is not a value; record a setting nobody has found with --unknown."));
    }
    let v = match pyjson::loads(text) {
        // a number too large to hold (an id like 5310427e48210936) is the text it is
        Ok(Value::Float(f)) if !f.is_finite() && !["Infinity", "-Infinity", "NaN"].contains(&text.trim()) => return Ok(Value::Str(text.to_string())),
        Ok(v) => v,
        Err(_) => return Ok(Value::Str(text.to_string())),
    };
    fn scalar(x: &Value) -> bool {
        match x {
            Value::Bool(_) | Value::Int(_) | Value::BigInt(_) => true,
            Value::Str(s) => !strip(s).is_empty(),
            Value::Float(f) => f.is_finite(),
            _ => false,
        }
    }
    if scalar(&v) || matches!(&v, Value::List(l) if !l.is_empty() && l.iter().all(scalar)) {
        return Ok(v);
    }
    if v.is_null() {
        return Err(RBError::msg("E_OBJECT_INVALID", "null is not a value; record a setting nobody has found with --unknown."));
    }
    Err(RBError::msg("E_OBJECT_INVALID", format!("A value is a finite number, true/false, non-empty text, or a non-empty list of those; got {}.", repr_str(text))))
}

/// `path#key`, `path:LINE`, `path` with --quote, `run:PATH#/pointer`, `https://...`, `note:text`.
pub fn parse_source(spec: Option<&str>, quote: Option<&str>, commit: Option<&str>, locator: Option<&str>, term: Option<&str>, root: &Path) -> Res<Option<Source>> {
    let Some(spec) = spec else {
        if [quote, commit, locator, term].iter().any(|x| x.map_or(false, |s| !s.is_empty())) {
            return Err(RBError::msg("E_OBJECT_INVALID", "--quote, --commit, --locator and --term describe a --source; give the --source too."));
        }
        return Ok(None);
    };
    let mut fields = Dict::new();
    if let Some(l) = locator {
        fields.insert("locator".into(), Value::Str(l.into()));
    }
    let kind: &str;
    if let Some(rest) = spec.strip_prefix("run:") {
        let (path, sep, pointer) = crate::ledger::rpartition(rest, "#");
        if !sep || path.is_empty() {
            return Err(RBError::msg(
                "E_OBJECT_INVALID",
                format!("A run source is run:PATH#/json/pointer, e.g. run:out/seed1/record.json#/seeds/torch (got {}).", repr_str(spec)),
            ));
        }
        kind = "run";
        fields.insert("kind".into(), "run".into());
        fields.insert("path".into(), Value::Str(root_relative(path, root)));
        fields.insert("pointer".into(), Value::Str(pointer.into()));
    } else if spec.starts_with("http://") || spec.starts_with("https://") {
        kind = "url";
        fields.insert("kind".into(), "url".into());
        fields.insert("url".into(), Value::Str(spec.into()));
        if let Some(q) = quote {
            fields.insert("quote".into(), Value::Str(q.into()));
        }
    } else if let Some(note) = spec.strip_prefix("note:") {
        kind = "note";
        fields.insert("kind".into(), "note".into());
        fields.insert("note".into(), Value::Str(strip(note).into()));
    } else {
        kind = "file";
        let (mut path, mut key, mut line): (String, Option<String>, Option<i64>) = (spec.to_string(), None, None);
        let (head, sep, tail) = crate::ledger::partition(spec, "#");
        // a key path reads a YAML, JSON or TOML file, or names a column of one row of a SQLite database
        if sep && !tail.is_empty() && (sources::structured_kind(head).is_some() || crate::sqlite::is_database(Path::new(head))) {
            path = head.to_string();
            key = Some(tail.to_string());
        } else {
            let (h2, sep2, t2) = crate::ledger::rpartition(spec, ":");
            if sep2 && !t2.is_empty() && t2.chars().all(|c| c.is_ascii_digit()) && !h2.is_empty() {
                path = h2.to_string();
                line = t2.parse::<i64>().ok();
            }
        }
        fields.insert("kind".into(), "file".into());
        fields.insert("path".into(), Value::Str(root_relative(&path, root)));
        if let Some(k) = key {
            fields.insert("key".into(), Value::Str(k));
        }
        if let Some(l) = line {
            fields.insert("line".into(), Value::Int(l));
        }
        if let Some(q) = quote {
            fields.insert("quote".into(), Value::Str(q.into()));
        }
        if let Some(c) = commit {
            fields.insert("commit".into(), Value::Str(c.into()));
        }
        if let Some(t) = term {
            fields.insert("term".into(), Value::Str(t.into()));
        }
    }
    if kind != "file" && (commit.map_or(false, |c| !c.is_empty()) || term.map_or(false, |t| !t.is_empty())) {
        return Err(RBError::msg("E_OBJECT_INVALID", "--commit and --term apply to a file source."));
    }
    if (kind == "run" || kind == "note") && quote.map_or(false, |q| !q.is_empty()) {
        return Err(RBError::msg("E_OBJECT_INVALID", format!("--quote applies to a file or url source; a {kind} source has no text to quote.")));
    }
    let mut errs = Vec::new();
    let mut loc = Vec::new();
    match Source::validate(&Value::Dict(fields), &mut loc, &mut errs) {
        Some(src) if errs.is_empty() => Ok(Some(src)),
        _ => Err(RBError::msg("E_OBJECT_INVALID", format!("--source {spec}: {}", errs.first().map(|e| e.msg.clone()).unwrap_or_default()))),
    }
}

/// NAME=VALUE pairs: numbers for metrics, values for --set, text otherwise.
pub fn parse_pairs(items: &[String], what: &str, numeric: bool) -> Res<Dict> {
    let mut out = Dict::new();
    for item in items {
        let (key, sep, val) = crate::ledger::partition(item, "=");
        if !sep || key.is_empty() {
            return Err(RBError::msg("E_OBJECT_INVALID", format!("{what} is name=value (got {}).", repr_str(item))));
        }
        if let Some(prev) = out.get(key) {
            return Err(RBError::msg("E_OBJECT_INVALID", format!("{what} {key} is given twice ({} and {val}); give each name once.", pyjson::py_str(prev))));
        }
        let v = if numeric {
            let Some(x) = crate::pyfloat::parse_float(val) else {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("{what} {key}: {} is not a number.", repr_str(val))));
            };
            if !x.is_finite() {
                return Err(RBError::msg("E_OBJECT_INVALID", format!("{what} {key} must be finite.")));
            }
            Value::Float(x)
        } else if matches!(what, "--set" | "--where" | "--subject-where" | "--reference-where") {
            parse_value(val)?
        } else {
            Value::Str(val.to_string())
        };
        out.insert(key.to_string(), v);
    }
    Ok(out)
}

/// --input imagenet-val or --input imagenet-val:eval-split: a resource id, and the role it played.
pub fn parse_uses(items: &[String], flag: &str) -> Res<Vec<(String, String)>> {
    let mut out = Vec::new();
    for item in items {
        let (rid, _, role) = crate::ledger::partition(item, ":");
        if rid.is_empty() {
            return Err(RBError::msg("E_OBJECT_INVALID", format!("{flag} takes a resource id, optionally :role (got {}).", repr_str(item))));
        }
        out.push((rid.to_string(), role.to_string()));
    }
    Ok(out)
}

/// A file named on the command line, as text; `shown` is the path as it was given.
pub fn read_text(path: &Path, shown: &str) -> Res<String> {
    match std::fs::read(path) {
        Ok(b) => Ok(String::from_utf8_lossy(&b).into_owned()),
        Err(e) => Err(RBError::could_not_read(&e, shown)),
    }
}

/// Numbers in a JSON (or YAML, TOML) file, nested keys joined with dots; everything else is named rather than dropped.
pub fn metrics_from_file(path: &str) -> Res<(Dict, Vec<String>)> {
    let shown = pathlib_str(path);
    let kind = sources::structured_kind(&shown).unwrap_or("json");
    let text = read_text(Path::new(path), &shown)?;
    let text = text.strip_prefix('\u{feff}').map(|s| s.to_string()).unwrap_or(text);
    let doc = sources::parse_structured(&text, kind, &shown).map_err(|u| RBError::msg("E_OBJECT_INVALID", u.reason))?;
    if !doc.is_dict() {
        return Err(RBError::msg("E_OBJECT_INVALID", format!("{shown} must hold an object of metric names to numbers.")));
    }
    let mut numbers = Dict::new();
    let mut skipped: Vec<String> = Vec::new();
    fn walk(prefix: &str, node: &Value, numbers: &mut Dict, skipped: &mut Vec<String>, shown: &str) -> Res<()> {
        match node {
            Value::Dict(d) if !d.is_empty() => {
                for (k, v) in d {
                    walk(&if prefix.is_empty() { k.clone() } else { format!("{prefix}.{k}") }, v, numbers, skipped, shown)?;
                }
            }
            Value::Int(_) | Value::Float(_) | Value::BigInt(_) if node.as_f64().map_or(false, |x| x.is_finite()) => {
                if numbers.contains_key(prefix) {
                    return Err(RBError::msg("E_OBJECT_INVALID", format!("{shown}: two keys both read as {} once nested keys are joined with dots.", repr_str(prefix))));
                }
                numbers.insert(prefix.to_string(), Value::Float(node.as_f64().unwrap()));
            }
            _ => skipped.push(prefix.to_string()),
        }
        Ok(())
    }
    walk("", &doc, &mut numbers, &mut skipped, &shown)?;
    Ok((numbers, skipped))
}

pub fn split_pairs(tokens: &[String]) -> Vec<String> {
    tokens.iter().filter(|t| t.contains('=')).cloned().collect()
}

// ---------------------------------------------------------------- what every command says

/// After a write: who it was recorded as goes in --json (`data.actor`), never in the text. The text says only that an
/// RB_ACTOR given in an agent session was ignored.
pub fn actor_line(out: &mut Out, led: &Ledger) -> Res<()> {
    let a = led.actor()?;
    if a.runtime.is_some() && a.ignored.as_ref().map_or(false, |ig| !ig.is_empty()) {
        out.say("· RB_ACTOR is ignored in an agent session");
    }
    out.data.insert("actor".into(), Value::Dict(actor_value(led, &a)));
    Ok(())
}

/// The actor as --json gives it: who, how rb knew, an RB_ACTOR it ignored, and for an agent what rb knows of it
/// (`agent`, as its writes record it).
pub fn actor_value(led: &Ledger, a: &crate::actor::Actor) -> Dict {
    let mut d = crate::dict! {"id" => a.id.clone(), "via" => a.via.clone()};
    if let Some(ig) = a.ignored.as_ref().filter(|s| !s.is_empty()) {
        d.insert("ignored_rb_actor".into(), Value::Str(ig.clone()));
    }
    if let Some(agent) = led.agent_facts(a, true) {
        d.insert("agent".into(), Value::Dict(agent));
    }
    d
}

/// {"kind": kind, **obj.model_dump(mode="json")}
pub fn obj(kind: &str, dump: Dict) -> Value {
    let mut d = crate::dict! {"kind" => kind};
    for (k, v) in dump {
        d.insert(k, v);
    }
    Value::Dict(d)
}

/// The words given with a write by `by`, verbatim, after the reason: `your words:` when the user gave them in their own
/// terminal, `words (as given):` when an agent session gave them, as view::say_words says them.
pub fn words(w: &str, by: &str) -> String {
    match crate::view::your_words(w, by) {
        said if said.is_empty() => String::new(),
        said => format!(" · {said}"),
    }
}

pub fn amend(ns: &Namespace) -> Res<Option<String>> {
    if flag(ns, "amend") {
        match s(ns, "why").filter(|w| !w.is_empty()) {
            Some(w) => Ok(Some(w)),
            None => Err(RBError::msg("E_OBJECT_INVALID", "--amend needs --why \"<reason>\": the reason is what the amendment records.")),
        }
    } else {
        Ok(None)
    }
}

pub fn again(ns: &Namespace) -> Res<Option<String>> {
    if flag(ns, "again") {
        match s(ns, "why").filter(|w| !w.is_empty()) {
            Some(w) => Ok(Some(w)),
            None => Err(RBError::msg("E_OBJECT_INVALID", "--again needs --why \"<reason>\": a deliberate repeat says why.")),
        }
    } else {
        Ok(None)
    }
}

/// A long value in a table cell: its start, enough to tell the rows apart.
pub fn short(text: &str, width: usize) -> String {
    if text.chars().count() <= width {
        text.to_string()
    } else {
        format!("{}…", trunc(text, width - 1))
    }
}

/// A long command in a title: its start and its end, where what tells two runs of it apart (a seed, a file) most
/// often is, the middle left out.
pub fn short_middle(text: &str, width: usize) -> String {
    let chars: Vec<char> = text.chars().collect();
    if chars.len() <= width {
        return text.to_string();
    }
    let head = (width - 1) * 2 / 5;
    let tail = width - 1 - head;
    format!("{}…{}", chars[..head].iter().collect::<String>(), chars[chars.len() - tail..].iter().collect::<String>())
}

pub fn tally_order(key: &str) -> usize {
    TALLY_ORDER.iter().position(|k| *k == key).unwrap_or(TALLY_ORDER.len())
}

/// Where the exact code of a run recorded from a tree with uncommitted changes is, and how to see it.
pub fn exact_code(root: &Path, attached: &Dict) -> Vec<String> {
    exact_code_at(root, attached, false, true)
}

/// The code a receipt kept: the exact code only where rb's journal watched the command run (`watched`), else the code
/// when the run was recorded, and not when it ran for a run made earlier. It is said to be kept in this repository only
/// where git here has the tree: a clone does not carry rb's refs.
pub fn exact_code_at(root: &Path, attached: &Dict, made_before: bool, watched: bool) -> Vec<String> {
    let tree = attached.get("tree").filter(|v| v.truthy()).map(pyjson::py_str);
    let base = trunc(&attached.get("commit").filter(|v| v.truthy()).map(pyjson::py_str).unwrap_or_default(), 12);
    let Some(tree) = tree else {
        return match attached.get("left_out").filter(|v| v.truthy()) {
            Some(n) => vec![format!("  The code was not kept: {} changed or untracked files were too many to snapshot.", pyjson::py_str(n))],
            None => Vec::new(),
        };
    };
    let t12 = trunc(&tree, 12);
    // a write rb only previews keeps no tree: the write does
    let dry = crate::dry::active();
    let here = !dry && crate::sources::git(root, &["cat-file", "-e", &format!("{tree}^{{tree}}")]).is_some();
    let what = if made_before {
        "The code when it was recorded, not when it ran"
    } else if watched {
        "The exact code"
    } else {
        "The code when it was recorded"
    };
    let mut lines = vec![if dry {
        format!("  {what}: git tree {t12}, which the write keeps in this repository under refs/rb/snapshots/.")
    } else if here {
        format!("  {what}: git tree {t12}, kept in this repository under refs/rb/snapshots/.")
    } else {
        format!("  {what}: git tree {t12}, not in this clone: the clone that recorded the run kept it.")
    }];
    if !base.is_empty() && here {
        lines.push(format!("  See the changes: git diff {base} {t12}   Restore a file: git checkout {t12} -- <path>"));
    }
    if let Some(n) = attached.get("left_out").filter(|v| v.truthy()) {
        let one = n.as_i64() == Some(1);
        lines.push(format!("  {} {} over 10 MB {} not in it.", pyjson::py_str(n), if one { "file" } else { "files" }, if one { "is" } else { "are" }));
    }
    if let Some(n) = attached.get("nested").filter(|v| v.truthy()) {
        let one = n.as_i64() == Some(1);
        lines.push(format!("  {} nested {} not in it.", pyjson::py_str(n), if one { "repository is" } else { "repositories are" }));
    }
    lines
}

pub fn get_s(d: &Dict, k: &str) -> String {
    d.get(k).map(|v| v.as_str().map(|s| s.to_string()).unwrap_or_else(|| pyjson::py_str(v))).unwrap_or_default()
}

pub fn dict_of(v: &Value) -> Dict {
    v.as_dict().cloned().unwrap_or_default()
}

pub fn show_v(v: &Value) -> String {
    show(v)
}
