//! A project's own YAML configs (Hydra, W&B, Lightning, plain training configs), read the way PyYAML reads them:
//! PyYAML's safe loader, YAML 1.1, with rb's two changes (`1e-4` is a float, dates stay text). Also the composed tree
//! with line numbers, for turning a config line into its key path and back. Records are never read here: they are
//! Rabbit Brain YAML (rbyaml).

use crate::pyfloat;
use crate::value::{Dict, Value};
use std::collections::HashMap;
use std::sync::OnceLock;
use yaml_rust2::parser::{Event, Parser, Tag};
use yaml_rust2::scanner::{Marker, TScalarStyle};

#[derive(Debug, Clone)]
pub enum NodeKind {
    Scalar(String, TScalarStyle, Option<Tag>),
    Seq(Vec<Node>),
    Map(Vec<(Node, Node)>),
}

#[derive(Debug, Clone)]
pub struct Node {
    pub kind: NodeKind,
    /// 1-based, as `start_mark.line + 1`
    pub line: usize,
}

/// yaml.compose(text): the single document's node tree, None for an empty stream.
/// The 1-based line of the anchor or tag that opens a collection whose start yaml-rust2 marks at `index`: PyYAML
/// starts the node there.
fn anchor_line(chars: &[char], index: usize) -> usize {
    let mut i = index.min(chars.len());
    while i > 0 {
        i -= 1;
        let c = chars[i];
        if (c == '&' || c == '!') && (i == 0 || chars[i - 1].is_whitespace() || chars[i - 1] == ':' || chars[i - 1] == '-') {
            return chars[..i].iter().filter(|c| **c == '\n').count() + 1;
        }
    }
    chars[..index.min(chars.len())].iter().filter(|c| **c == '\n').count() + 1
}

/// The 1-based line of the last content before character `index`, comments and whitespace skipped: where PyYAML puts
/// an empty value (just after its ':' or '-').
fn prev_content_line(chars: &[char], index: usize) -> usize {
    let before: String = chars[..index.min(chars.len())].iter().collect();
    let lines: Vec<&str> = before.split('\n').collect();
    for (n, line) in lines.iter().enumerate().rev() {
        let mut cut = line.len();
        let b = line.as_bytes();
        for (i, c) in line.char_indices() {
            if c == '#' && (i == 0 || b[i - 1] == b' ' || b[i - 1] == b'\t') {
                cut = i;
                break;
            }
        }
        if !line[..cut].trim().is_empty() {
            return n + 1;
        }
    }
    1
}

pub fn compose(text: &str) -> Result<Option<Node>, String> {
    let chars: Vec<char> = text.chars().collect();
    let mut p = Parser::new_from_str(text);
    let mut stack: Vec<(Node, Option<Node>)> = Vec::new(); // (collection, pending key for maps)
    let mut anchors: HashMap<usize, Node> = HashMap::new();
    let mut anchor_of: Vec<usize> = Vec::new();
    let mut docs = 0;
    let mut root: Option<Node> = None;
    loop {
        let (ev, mark): (Event, Marker) = p.next_token().map_err(|e| e.to_string())?;
        let line = mark.line().max(1);
        let finished: Option<(Node, usize)> = match ev {
            Event::StreamEnd => break,
            Event::StreamStart | Event::Nothing | Event::DocumentEnd => None,
            Event::DocumentStart => {
                docs += 1;
                if docs > 1 {
                    return Err("expected a single document in the stream".into());
                }
                None
            }
            Event::Scalar(v, style, aid, tag) => {
                let line = if v.is_empty() && style == TScalarStyle::Plain && tag.is_none() { prev_content_line(&chars, mark.index()) } else { line };
                Some((Node { kind: NodeKind::Scalar(v, style, tag), line }, aid))
            }
            Event::Alias(id) => match anchors.get(&id) {
                Some(n) => Some((n.clone(), 0)),
                None => return Err(format!("found undefined alias {id}")),
            },
            Event::SequenceStart(aid, tag) => {
                let line = if aid != 0 || tag.is_some() { anchor_line(&chars, mark.index()) } else { line };
                stack.push((Node { kind: NodeKind::Seq(Vec::new()), line }, None));
                anchor_of.push(aid);
                None
            }
            Event::MappingStart(aid, tag) => {
                let line = if aid != 0 || tag.is_some() { anchor_line(&chars, mark.index()) } else { line };
                stack.push((Node { kind: NodeKind::Map(Vec::new()), line }, None));
                anchor_of.push(aid);
                None
            }
            Event::SequenceEnd | Event::MappingEnd => {
                let (n, _) = stack.pop().unwrap();
                let aid = anchor_of.pop().unwrap_or(0);
                Some((n, aid))
            }
        };
        if let Some((node, aid)) = finished {
            if aid != 0 {
                anchors.insert(aid, node.clone());
            }
            match stack.last_mut() {
                None => root = Some(node),
                Some((parent, pending)) => match &mut parent.kind {
                    NodeKind::Seq(items) => items.push(node),
                    NodeKind::Map(pairs) => match pending.take() {
                        None => *pending = Some(node),
                        Some(k) => pairs.push((k, node)),
                    },
                    NodeKind::Scalar(..) => unreachable!(),
                },
            }
        }
    }
    Ok(root)
}

// ---------------------------------------------------------------- resolving plain scalars as PyYAML's SafeLoader does

fn re(cell: &'static OnceLock<regex::Regex>, pat: &str) -> &'static regex::Regex {
    cell.get_or_init(|| regex::Regex::new(pat).unwrap())
}

fn re_bool() -> &'static regex::Regex {
    static R: OnceLock<regex::Regex> = OnceLock::new();
    re(&R, r"^(?:yes|Yes|YES|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF)$")
}
fn re_float() -> &'static regex::Regex {
    static R: OnceLock<regex::Regex> = OnceLock::new();
    re(
        &R,
        r"^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?|\.[0-9_]+(?:[eE][-+][0-9]+)?|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$",
    )
}
fn re_float_rb() -> &'static regex::Regex {
    static R: OnceLock<regex::Regex> = OnceLock::new();
    re(&R, r"^[-+]?(?:[0-9][0-9_]*(?:\.[0-9_]*)?|\.[0-9_]+)[eE][-+]?[0-9]+$")
}
fn re_int() -> &'static regex::Regex {
    static R: OnceLock<regex::Regex> = OnceLock::new();
    re(&R, r"^(?:[-+]?0b[0-1_]+|[-+]?0[0-7_]+|[-+]?(?:0|[1-9][0-9_]*)|[-+]?0x[0-9a-fA-F_]+|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+)$")
}
fn re_null() -> &'static regex::Regex {
    static R: OnceLock<regex::Regex> = OnceLock::new();
    re(&R, r"^(?:~|null|Null|NULL|)$")
}

#[derive(PartialEq)]
enum Res {
    Bool,
    Float,
    Int,
    Merge,
    Null,
    Value,
    Str,
}

fn resolve_plain(v: &str) -> Res {
    let first = v.chars().next();
    // the resolvers PyYAML keeps for the scalar's first character, in the order they were added
    let mut order: Vec<Res> = Vec::new();
    match first {
        None => order.push(Res::Null),
        Some(c) => {
            if "yYnNtTfFoO".contains(c) {
                order.push(Res::Bool);
            }
            if "-+0123456789.".contains(c) {
                order.push(Res::Float);
            }
            if "-+0123456789".contains(c) {
                order.push(Res::Int);
            }
            if c == '<' {
                order.push(Res::Merge);
            }
            if "~nN".contains(c) {
                order.push(Res::Null);
            }
            if c == '=' {
                order.push(Res::Value);
            }
            if "-+0123456789.".contains(c) {
                order.push(Res::Float); // rb's: 1e-4 without a dot
            }
        }
    }
    let mut float_seen = false;
    for r in order {
        let hit = match r {
            Res::Bool => re_bool().is_match(v),
            Res::Float => {
                let hit = if !float_seen { re_float().is_match(v) } else { re_float_rb().is_match(v) };
                float_seen = true;
                hit
            }
            Res::Int => re_int().is_match(v),
            Res::Merge => v == "<<",
            Res::Null => re_null().is_match(v),
            Res::Value => v == "=",
            Res::Str => false,
        };
        if hit {
            return r;
        }
    }
    Res::Str
}

fn sexagesimal(digits: &str) -> f64 {
    let mut base = 1f64;
    let mut value = 0f64;
    let mut parts: Vec<f64> = digits.split(':').map(|p| p.parse::<f64>().unwrap_or(0.0)).collect();
    parts.reverse();
    for d in parts {
        value += d * base;
        base *= 60.0;
    }
    value
}

fn construct_int(v: &str) -> Value {
    let s = v.replace('_', "");
    let (neg, s) = match s.strip_prefix('-') {
        Some(r) => (true, r.to_string()),
        None => (false, s.strip_prefix('+').unwrap_or(&s).to_string()),
    };
    let sign = if neg { -1i128 } else { 1 };
    let parsed: Option<i128> = if s == "0" {
        Some(0)
    } else if let Some(b) = s.strip_prefix("0b") {
        i128::from_str_radix(b, 2).ok()
    } else if let Some(h) = s.strip_prefix("0x") {
        i128::from_str_radix(h, 16).ok()
    } else if s.starts_with('0') {
        i128::from_str_radix(&s, 8).ok()
    } else if s.contains(':') {
        let mut base = 1i128;
        let mut value = 0i128;
        let mut parts: Vec<i128> = s.split(':').map(|p| p.parse::<i128>().unwrap_or(0)).collect();
        parts.reverse();
        for d in parts {
            value += d * base;
            base *= 60;
        }
        Some(value)
    } else {
        return pyfloat::int_value(neg, &s);
    };
    match parsed {
        Some(x) => {
            let x = sign * x;
            if x >= i64::MIN as i128 && x <= i64::MAX as i128 {
                Value::Int(x as i64)
            } else {
                Value::BigInt(x.to_string())
            }
        }
        None => Value::Str(v.to_string()),
    }
}

fn construct_float(v: &str) -> Value {
    let s = v.replace('_', "").to_lowercase();
    let (sign, s) = match s.strip_prefix('-') {
        Some(r) => (-1.0, r.to_string()),
        None => (1.0, s.strip_prefix('+').unwrap_or(&s).to_string()),
    };
    if s == ".inf" {
        return Value::Float(sign * f64::INFINITY);
    }
    if s == ".nan" {
        return Value::Float(f64::NAN);
    }
    if s.contains(':') {
        return Value::Float(sign * sexagesimal(&s));
    }
    Value::Float(sign * pyfloat::parse_float(&s).unwrap_or(f64::NAN))
}

/// A dict key as json.dumps writes it: text as it is, true/false/null, numbers as Python prints them.
fn key_text(v: &Value) -> String {
    match v {
        Value::Str(s) => s.clone(),
        Value::Bool(true) => "true".into(),
        Value::Bool(false) => "false".into(),
        Value::Null => "null".into(),
        other => crate::pyjson::py_str(other),
    }
}

/// Which keys Python's dict treats as the same key.
fn key_class(v: &Value) -> String {
    match v {
        Value::Str(s) => format!("s:{s}"),
        Value::Null => "n".into(),
        Value::Bool(b) => format!("i:{}", *b as i64),
        Value::Int(i) => format!("i:{i}"),
        Value::BigInt(s) => format!("i:{s}"),
        Value::Float(f) if f.fract() == 0.0 && f.is_finite() => format!("i:{}", pyfloat::float_int_digits(*f)),
        Value::Float(f) => format!("f:{}", pyfloat::repr(*f)),
        _ => String::new(),
    }
}

fn construct(n: &Node) -> Result<Value, String> {
    match &n.kind {
        NodeKind::Scalar(v, style, tag) => {
            if let Some(t) = tag {
                let name = format!("{}{}", t.handle, t.suffix);
                return Ok(match name.as_str() {
                    "!!str" | "tag:yaml.org,2002:str" => Value::Str(v.clone()),
                    "!!int" | "tag:yaml.org,2002:int" => construct_int(v),
                    "!!float" | "tag:yaml.org,2002:float" => construct_float(v),
                    "!!bool" | "tag:yaml.org,2002:bool" => Value::Bool(matches!(v.to_lowercase().as_str(), "yes" | "true" | "on")),
                    "!!null" | "tag:yaml.org,2002:null" => Value::Null,
                    other => return Err(format!("could not determine a constructor for the tag '{other}'")),
                });
            }
            if *style != TScalarStyle::Plain {
                return Ok(Value::Str(v.clone()));
            }
            Ok(match resolve_plain(v) {
                Res::Bool => Value::Bool(matches!(v.to_lowercase().as_str(), "yes" | "true" | "on")),
                Res::Float => construct_float(v),
                Res::Int => construct_int(v),
                Res::Null => Value::Null,
                Res::Merge | Res::Value => Value::Str(v.clone()),
                Res::Str => Value::Str(v.clone()),
            })
        }
        NodeKind::Seq(items) => Ok(Value::List(items.iter().map(construct).collect::<Result<_, _>>()?)),
        NodeKind::Map(pairs) => {
            let mut d = Dict::new();
            // merge keys first (PyYAML flattens them before the mapping's own keys)
            let mut own: Vec<(&Node, &Node)> = Vec::new();
            for (k, v) in pairs {
                if let NodeKind::Scalar(s, TScalarStyle::Plain, None) = &k.kind {
                    if s == "<<" {
                        let merged = construct(v)?;
                        let maps: Vec<Value> = match merged {
                            Value::Dict(_) => vec![merged],
                            Value::List(l) => l.into_iter().rev().collect(),
                            _ => return Err("expected a mapping or list of mappings for merging".into()),
                        };
                        for m in maps {
                            if let Value::Dict(md) = m {
                                for (mk, mv) in md {
                                    d.insert(mk, mv);
                                }
                            }
                        }
                        continue;
                    }
                }
                own.push((k, v));
            }
            // a Python dict: keys equal to Python (1, 1.0 and True) are one key, kept as first written
            let mut first_text: HashMap<String, String> = HashMap::new();
            for (k, v) in own {
                let key = construct(k)?;
                if matches!(key, Value::List(_) | Value::Dict(_)) {
                    return Err("found a list or a mapping where a key belongs".into());
                }
                let class = key_class(&key);
                let text = first_text.entry(class).or_insert_with(|| key_text(&key)).clone();
                d.insert(text, construct(v)?);
            }
            Ok(Value::Dict(d))
        }
    }
}

/// yaml.load(text, Loader=rb's safe loader).
pub fn load(text: &str) -> Result<Value, String> {
    match compose(text)? {
        None => Ok(Value::Null),
        Some(n) => construct(&n),
    }
}

// ---------------------------------------------------------------- lines and key paths

/// The line holding the value at `key` in a YAML file, from the parser's own positions.
pub fn line_of(text: &str, steps: &[String]) -> Option<usize> {
    let mut node = compose(text).ok()??;
    for step in steps {
        let next = match &node.kind {
            NodeKind::Map(pairs) => pairs
                .iter()
                .find(|(k, _)| matches!(&k.kind, NodeKind::Scalar(s, _, _) if s == step))
                .map(|(_, v)| v.clone()),
            NodeKind::Seq(items) => {
                if !step.is_empty() && step.chars().all(|c| c.is_ascii_digit()) {
                    step.parse::<usize>().ok().and_then(|i| items.get(i).cloned())
                } else {
                    None
                }
            }
            NodeKind::Scalar(..) => None,
        };
        node = next?;
    }
    Some(node.line)
}

/// The key path whose scalar value sits on `line` of a YAML file, or None.
pub fn key_at(text: &str, line: usize) -> Option<String> {
    let root = compose(text).ok()??;
    fn walk(node: &Node, path: &mut Vec<String>, line: usize) -> Option<String> {
        match &node.kind {
            NodeKind::Map(pairs) => {
                for (k, v) in pairs {
                    let kt = match &k.kind {
                        NodeKind::Scalar(s, _, _) => s.clone(),
                        _ => String::new(),
                    };
                    path.push(kt);
                    let hit = walk(v, path, line);
                    path.pop();
                    if hit.is_some() {
                        return hit;
                    }
                }
                None
            }
            NodeKind::Seq(items) => {
                if node.line == line && !path.is_empty() && items.iter().all(|x| matches!(x.kind, NodeKind::Scalar(..))) {
                    return Some(path.join("."));
                }
                for (i, v) in items.iter().enumerate() {
                    path.push(i.to_string());
                    let hit = walk(v, path, line);
                    path.pop();
                    if hit.is_some() {
                        return hit;
                    }
                }
                None
            }
            NodeKind::Scalar(..) => {
                if node.line == line && !path.is_empty() {
                    Some(path.join("."))
                } else {
                    None
                }
            }
        }
    }
    walk(&root, &mut Vec::new(), line)
}
