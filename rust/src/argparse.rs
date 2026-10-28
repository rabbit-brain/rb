//! Python 3.11's argparse, ported for rb's command line: the same parsing (prefix matching of long options, negative
//! numbers as values, nargs patterns, subparsers, mutually exclusive groups), the same error messages, and the same help
//! and usage text. The tree of parsers is rb's command line as it is declared (`cli_parser.py`), written to
//! `cli_spec.json` by `rust/gen/gen_cli_spec.py`.

use crate::pyjson;
use crate::value::{Dict, Value};
use std::collections::{HashMap, HashSet};
use std::sync::OnceLock;

pub const SUPPRESS: &str = "==SUPPRESS==";

#[derive(Debug, Clone, PartialEq)]
pub enum Nargs {
    One, // None: exactly one, as a single value
    Optional,
    ZeroOrMore,
    OneOrMore,
    Remainder,
    Parser,
    Suppress,
    Exactly(usize),
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Kind {
    Store,
    StoreTrue,
    StoreFalse,
    StoreConst,
    Append,
    AppendConst,
    Count,
    Help,
    Version,
    Parsers,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Ty {
    Int,
    Float,
    Str,
}

#[derive(Debug, Clone)]
pub struct ChoiceAction {
    pub dest: String,
    pub metavar: String,
    pub help: Option<String>,
}

#[derive(Debug, Clone)]
pub struct Action {
    pub kind: Kind,
    pub options: Vec<String>,
    pub dest: String,
    pub nargs: Nargs,
    pub konst: Value,
    /// None: the default is SUPPRESS
    pub default: Option<Value>,
    pub ty: Option<Ty>,
    pub choices: Option<Vec<Value>>,
    pub required: bool,
    /// None: no help; Some(SUPPRESS): hidden
    pub help: Option<String>,
    pub metavar: Option<String>,
    pub version: Option<String>,
    pub parsers: Vec<(String, Parser)>,
    pub choice_actions: Vec<ChoiceAction>,
}

impl Action {
    fn help_suppressed(&self) -> bool {
        self.help.as_deref() == Some(SUPPRESS)
    }
}

#[derive(Debug, Clone)]
pub struct Group {
    pub title: Option<String>,
    pub description: Option<String>,
    pub actions: Vec<usize>,
}

#[derive(Debug, Clone)]
pub struct Mutex {
    pub required: bool,
    pub actions: Vec<usize>,
}

#[derive(Debug, Clone)]
pub struct Parser {
    pub prog: String,
    pub usage: Option<String>,
    pub description: Option<String>,
    pub epilog: Option<String>,
    pub actions: Vec<Action>,
    pub groups: Vec<Group>,
    pub mutex: Vec<Mutex>,
    pub defaults: Vec<(String, Value)>,
    option_index: HashMap<String, usize>,
}

/// What the parse found: dest -> value, in the order Python's Namespace would hold them.
pub type Namespace = Dict;

/// argparse could not read the command line: the message, and the usage of the parser that said so.
#[derive(Debug, Clone)]
pub struct UsageError {
    pub message: String,
    pub usage: String,
}

/// What parsing ended with, other than a namespace.
#[derive(Debug)]
pub enum Stop {
    Usage(UsageError),
    /// -h/--help or --version printed its text: exit with this code
    Exit(i32, String),
}

// ---------------------------------------------------------------- building the tree from the spec

fn nargs_of(v: Option<&Value>) -> Nargs {
    match v {
        None | Some(Value::Null) => Nargs::One,
        Some(Value::Int(n)) => Nargs::Exactly(*n as usize),
        Some(Value::Str(s)) => match s.as_str() {
            "?" => Nargs::Optional,
            "*" => Nargs::ZeroOrMore,
            "+" => Nargs::OneOrMore,
            "..." => Nargs::Remainder,
            "A..." => Nargs::Parser,
            SUPPRESS => Nargs::Suppress,
            other => panic!("nargs {other}"),
        },
        Some(other) => panic!("nargs {other:?}"),
    }
}

fn action_of(v: &Value) -> Action {
    let kind = match v.get_str("kind").unwrap() {
        "store" => Kind::Store,
        "store_true" => Kind::StoreTrue,
        "store_false" => Kind::StoreFalse,
        "store_const" => Kind::StoreConst,
        "append" => Kind::Append,
        "append_const" => Kind::AppendConst,
        "count" => Kind::Count,
        "help" => Kind::Help,
        "version" => Kind::Version,
        "parsers" => Kind::Parsers,
        k => panic!("action kind {k}"),
    };
    let default = match v.get("default") {
        Some(Value::Str(s)) if s == SUPPRESS => None,
        Some(d) => Some(d.clone()),
        None => Some(Value::Null),
    };
    let parsers = match v.get("parsers") {
        Some(Value::List(items)) => items
            .iter()
            .map(|pair| {
                let p = pair.as_list().unwrap();
                (p[0].as_str().unwrap().to_string(), parser_of(&p[1]))
            })
            .collect(),
        _ => Vec::new(),
    };
    let choice_actions = match v.get("choice_actions") {
        Some(Value::List(items)) => items
            .iter()
            .map(|c| ChoiceAction {
                dest: c.get_str("dest").unwrap().to_string(),
                metavar: c.get_str("metavar").unwrap_or("").to_string(),
                help: c.get_str("help").map(|s| s.to_string()),
            })
            .collect(),
        _ => Vec::new(),
    };
    Action {
        kind,
        options: v.get("options").and_then(|o| o.as_list()).map(|l| l.iter().map(|x| x.as_str().unwrap().to_string()).collect()).unwrap_or_default(),
        dest: v.get_str("dest").unwrap_or(SUPPRESS).to_string(),
        nargs: nargs_of(v.get("nargs")),
        konst: v.get("const").cloned().unwrap_or(Value::Null),
        default,
        ty: match v.get_str("type") {
            Some("int") => Some(Ty::Int),
            Some("float") => Some(Ty::Float),
            Some("str") => Some(Ty::Str),
            None => None,
            Some(t) => panic!("type {t}"),
        },
        choices: v.get("choices").and_then(|c| c.as_list()).cloned(),
        required: v.get("required").and_then(|r| r.as_bool()).unwrap_or(false),
        help: v.get_str("help").map(|s| s.to_string()),
        metavar: v.get_str("metavar").map(|s| s.to_string()),
        // the spec says where the version goes; rb says its own (so --version and `rb version` cannot disagree)
        version: v.get_str("version").map(|s| s.replace("{version}", crate::version())),
        parsers,
        choice_actions,
    }
}

fn parser_of(v: &Value) -> Parser {
    let actions: Vec<Action> = v.get("actions").and_then(|a| a.as_list()).unwrap().iter().map(action_of).collect();
    let idx_list = |g: &Value| -> Vec<usize> { g.get("actions").and_then(|a| a.as_list()).unwrap().iter().map(|n| n.as_i64().unwrap() as usize).collect() };
    let groups = v
        .get("groups")
        .and_then(|g| g.as_list())
        .map(|gs| {
            gs.iter()
                .map(|g| Group { title: g.get_str("title").map(|s| s.to_string()), description: g.get_str("description").map(|s| s.to_string()), actions: idx_list(g) })
                .collect()
        })
        .unwrap_or_default();
    let mutex = v
        .get("mutex")
        .and_then(|g| g.as_list())
        .map(|gs| gs.iter().map(|g| Mutex { required: g.get("required").and_then(|r| r.as_bool()).unwrap_or(false), actions: idx_list(g) }).collect())
        .unwrap_or_default();
    let defaults = v.get("defaults").and_then(|d| d.as_dict()).map(|d| d.iter().map(|(k, v)| (k.clone(), v.clone())).collect()).unwrap_or_default();
    let mut option_index = HashMap::new();
    for (n, a) in actions.iter().enumerate() {
        for o in &a.options {
            option_index.insert(o.clone(), n);
        }
    }
    Parser {
        prog: v.get_str("prog").unwrap().to_string(),
        usage: v.get_str("usage").map(|s| s.to_string()),
        description: v.get_str("description").map(|s| s.to_string()),
        epilog: v.get_str("epilog").map(|s| s.to_string()),
        actions,
        groups,
        mutex,
        defaults,
        option_index,
    }
}

/// rb's parser tree, built once from the embedded spec.
pub fn rb_parser() -> &'static Parser {
    static P: OnceLock<Parser> = OnceLock::new();
    P.get_or_init(|| {
        let spec = pyjson::loads(include_str!("cli_spec.json")).expect("cli_spec.json");
        parser_of(&spec)
    })
}

// ---------------------------------------------------------------- parsing

fn negative_number(s: &str) -> bool {
    static R: OnceLock<regex::Regex> = OnceLock::new();
    R.get_or_init(|| regex::Regex::new(r"^-(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$").unwrap()).is_match(s)
}

/// (action index or None, option string, sep, explicit arg)
type OptTuple = (Option<usize>, String, Option<String>, Option<String>);

fn action_name(a: &Action) -> Option<String> {
    if !a.options.is_empty() {
        Some(a.options.join("/"))
    } else if let Some(m) = &a.metavar {
        if m != SUPPRESS {
            return Some(m.clone());
        }
        None
    } else if a.dest != SUPPRESS {
        Some(a.dest.clone())
    } else {
        a.choices.as_ref().map(|c| format!("{{{}}}", c.iter().map(pyjson::py_str).collect::<Vec<_>>().join(",")))
    }
}

/// An ArgumentError: str() is "argument NAME: MESSAGE" when the action has a name.
fn arg_error(a: &Action, msg: String) -> String {
    match action_name(a) {
        Some(n) => format!("argument {n}: {msg}"),
        None => msg,
    }
}

fn nargs_pattern(a: &Action) -> String {
    let p = match &a.nargs {
        Nargs::One => "(-*A-*)".to_string(),
        Nargs::Optional => "(-*A?-*)".to_string(),
        Nargs::ZeroOrMore => "(-*[A-]*)".to_string(),
        Nargs::OneOrMore => "(-*A[A-]*)".to_string(),
        Nargs::Remainder => "([-AO]*)".to_string(),
        Nargs::Parser => "(-*A[-AO]*)".to_string(),
        Nargs::Suppress => "(-*-*)".to_string(),
        Nargs::Exactly(n) => format!("(-*{}-*)", vec!["A"; *n].join("-*")),
    };
    if !a.options.is_empty() {
        p.replace("-*", "").replace('-', "")
    } else {
        p
    }
}

fn re_match_groups(pattern: &str, text: &str) -> Option<Vec<usize>> {
    // Python's re.match: anchored at the start, not the end
    let re = regex::Regex::new(&format!("^(?:{pattern})")).ok()?;
    let caps = re.captures(text)?;
    Some((1..caps.len()).map(|k| caps.get(k).map_or(0, |m| m.as_str().len())).collect())
}

impl Parser {
    fn parse_optional(&self, arg: &str) -> Result<Option<OptTuple>, String> {
        if arg.is_empty() || !arg.starts_with('-') {
            return Ok(None);
        }
        if let Some(&i) = self.option_index.get(arg) {
            return Ok(Some((Some(i), arg.to_string(), None, None)));
        }
        if arg.chars().count() == 1 {
            return Ok(None);
        }
        if let Some((opt, explicit)) = arg.split_once('=') {
            if let Some(&i) = self.option_index.get(opt) {
                return Ok(Some((Some(i), opt.to_string(), Some("=".into()), Some(explicit.to_string()))));
            }
        }
        let tuples = self.option_tuples(arg)?;
        if tuples.len() > 1 {
            let options: Vec<String> = tuples.iter().map(|t| t.1.clone()).collect();
            return Err(format!("ambiguous option: {} could match {}", arg, options.join(", ")));
        } else if tuples.len() == 1 {
            return Ok(Some(tuples.into_iter().next().unwrap()));
        }
        if negative_number(arg) {
            return Ok(None); // rb has no options that look like negative numbers
        }
        if arg.contains(' ') {
            return Ok(None);
        }
        Ok(Some((None, arg.to_string(), None, None)))
    }

    fn option_tuples(&self, option_string: &str) -> Result<Vec<OptTuple>, String> {
        let mut result = Vec::new();
        let chars: Vec<char> = option_string.chars().collect();
        // iterate options in the order Python's dict holds them: the order they were added
        let mut ordered: Vec<(&String, usize)> = Vec::new();
        for (n, a) in self.actions.iter().enumerate() {
            for o in &a.options {
                ordered.push((o, n));
            }
        }
        if chars[0] == '-' && chars.len() > 1 && chars[1] == '-' {
            let (prefix, sep, explicit) = match option_string.split_once('=') {
                Some((p, e)) => (p.to_string(), Some("=".to_string()), Some(e.to_string())),
                None => (option_string.to_string(), None, None),
            };
            for (o, n) in &ordered {
                if o.starts_with(&prefix) {
                    result.push((Some(*n), (*o).clone(), sep.clone(), explicit.clone()));
                }
            }
        } else if chars[0] == '-' && chars.len() > 1 && chars[1] != '-' {
            let short_prefix: String = chars[..2].iter().collect();
            let short_explicit: String = chars[2..].iter().collect();
            for (o, n) in &ordered {
                if **o == short_prefix {
                    result.push((Some(*n), (*o).clone(), Some(String::new()), Some(short_explicit.clone())));
                } else if o.starts_with(option_string) {
                    result.push((Some(*n), (*o).clone(), None, None));
                }
            }
        } else {
            return Err(format!("unexpected option string: {option_string}"));
        }
        Ok(result)
    }

    fn match_argument(&self, a: &Action, pattern: &str) -> Result<usize, String> {
        match re_match_groups(&nargs_pattern(a), pattern) {
            Some(g) => Ok(g[0]),
            None => {
                let msg = match &a.nargs {
                    Nargs::One => "expected one argument".to_string(),
                    Nargs::Optional => "expected at most one argument".to_string(),
                    Nargs::OneOrMore => "expected at least one argument".to_string(),
                    Nargs::Exactly(n) => format!("expected {} argument{}", n, if *n == 1 { "" } else { "s" }),
                    _ => "expected one argument".to_string(),
                };
                Err(arg_error(a, msg))
            }
        }
    }

    fn match_partial(&self, positionals: &[usize], pattern: &str) -> Vec<usize> {
        for i in (1..=positionals.len()).rev() {
            let pat: String = positionals[..i].iter().map(|&k| nargs_pattern(&self.actions[k])).collect();
            if let Some(g) = re_match_groups(&pat, pattern) {
                return g;
            }
        }
        Vec::new()
    }

    fn get_value(&self, a: &Action, s: &str) -> Result<Value, String> {
        match a.ty {
            None | Some(Ty::Str) => Ok(Value::Str(s.to_string())),
            Some(Ty::Int) => crate::pyfloat::parse_int(s).ok_or_else(|| arg_error(a, format!("invalid int value: {}", pyjson::repr_str(s)))),
            Some(Ty::Float) => crate::pyfloat::parse_float(s).map(Value::Float).ok_or_else(|| arg_error(a, format!("invalid float value: {}", pyjson::repr_str(s)))),
        }
    }

    fn check_value(&self, a: &Action, v: &Value) -> Result<(), String> {
        if a.kind == Kind::Parsers {
            let name = v.as_str().unwrap_or("");
            if !a.parsers.iter().any(|(n, _)| n == name) {
                let choices: Vec<String> = a.parsers.iter().map(|(n, _)| pyjson::repr_str(n)).collect();
                return Err(arg_error(a, format!("invalid choice: {} (choose from {})", pyjson::py_repr(v), choices.join(", "))));
            }
            return Ok(());
        }
        if let Some(ch) = &a.choices {
            if !ch.iter().any(|c| c == v) {
                let choices: Vec<String> = ch.iter().map(pyjson::py_repr).collect();
                return Err(arg_error(a, format!("invalid choice: {} (choose from {})", pyjson::py_repr(v), choices.join(", "))));
            }
        }
        Ok(())
    }

    /// (value, whether it is the action's default object itself)
    fn get_values(&self, a: &Action, mut args: Vec<String>) -> Result<(Value, bool), String> {
        if a.options.is_empty() && a.nargs != Nargs::Parser && a.nargs != Nargs::Remainder {
            if let Some(p) = args.iter().position(|s| s == "--") {
                args.remove(p);
            }
        }
        if args.is_empty() && a.nargs == Nargs::Optional {
            let (value, is_default) = if !a.options.is_empty() { (a.konst.clone(), false) } else { (a.default.clone().unwrap_or(Value::Null), true) };
            if let Value::Str(s) = &value {
                let v = self.get_value(a, s)?;
                self.check_value(a, &v)?;
                return Ok((v, false));
            }
            return Ok((value, is_default));
        }
        if args.is_empty() && a.nargs == Nargs::ZeroOrMore && a.options.is_empty() {
            let (value, is_default) = match &a.default {
                Some(d) if !d.is_null() => (d.clone(), true),
                _ => (Value::List(Vec::new()), false),
            };
            self.check_value(a, &value)?;
            return Ok((value, is_default));
        }
        if args.len() == 1 && (a.nargs == Nargs::One || a.nargs == Nargs::Optional) {
            let v = self.get_value(a, &args[0])?;
            self.check_value(a, &v)?;
            return Ok((v, false));
        }
        match a.nargs {
            Nargs::Remainder => Ok((Value::List(args.iter().map(|s| self.get_value(a, s)).collect::<Result<_, _>>()?), false)),
            Nargs::Parser => {
                let vals: Vec<Value> = args.iter().map(|s| self.get_value(a, s)).collect::<Result<_, _>>()?;
                self.check_value(a, &vals[0])?;
                Ok((Value::List(vals), false))
            }
            Nargs::Suppress => Ok((Value::Str(SUPPRESS.into()), false)),
            _ => {
                let vals: Vec<Value> = args.iter().map(|s| self.get_value(a, s)).collect::<Result<_, _>>()?;
                for v in &vals {
                    self.check_value(a, v)?;
                }
                Ok((Value::List(vals), false))
            }
        }
    }

    fn usage_error(&self, message: String) -> Stop {
        Stop::Usage(UsageError { message, usage: self.format_usage() })
    }

    /// parse_args: the namespace, or why the command line could not be read (or help to print and exit).
    pub fn parse_args(&self, args: &[String]) -> Result<Namespace, Stop> {
        let (ns, extras) = self.parse_known_args(args, Namespace::new())?;
        if !extras.is_empty() {
            return Err(self.usage_error(format!("unrecognized arguments: {}", extras.join(" "))));
        }
        Ok(ns)
    }

    pub fn parse_known_args(&self, args: &[String], mut ns: Namespace) -> Result<(Namespace, Vec<String>), Stop> {
        for a in &self.actions {
            if a.dest != SUPPRESS && !ns.contains_key(&a.dest) {
                if let Some(d) = &a.default {
                    ns.insert(a.dest.clone(), d.clone());
                }
            }
        }
        for (k, v) in &self.defaults {
            if !ns.contains_key(k) {
                ns.insert(k.clone(), v.clone());
            }
        }
        let mut extras_from_sub: Vec<String> = Vec::new();
        match self.parse_inner(args, &mut ns, &mut extras_from_sub) {
            Ok(mut extras) => {
                extras.extend(extras_from_sub);
                Ok((ns, extras))
            }
            Err(Inner::Arg(msg)) => Err(self.usage_error(msg)),
            Err(Inner::Stop(s)) => Err(s),
        }
    }

    fn parse_inner(&self, arg_strings: &[String], ns: &mut Namespace, unrecognized: &mut Vec<String>) -> Result<Vec<String>, Inner> {
        // mutually exclusive conflicts
        let mut conflicts: HashMap<usize, Vec<usize>> = HashMap::new();
        for m in &self.mutex {
            for (i, &a) in m.actions.iter().enumerate() {
                let e = conflicts.entry(a).or_default();
                e.extend(m.actions[..i].iter().copied());
                e.extend(m.actions[i + 1..].iter().copied());
            }
        }
        // the pattern of option and argument strings
        let mut option_indices: HashMap<usize, OptTuple> = HashMap::new();
        let mut pattern = String::new();
        let mut it = arg_strings.iter().enumerate();
        while let Some((i, s)) = it.next() {
            if s == "--" {
                pattern.push('-');
                for _ in it.by_ref() {
                    pattern.push('A');
                }
                break;
            }
            match self.parse_optional(s).map_err(|m| Inner::Stop(self.usage_error(m)))? {
                None => pattern.push('A'),
                Some(t) => {
                    option_indices.insert(i, t);
                    pattern.push('O');
                }
            }
        }
        let mut seen: HashSet<usize> = HashSet::new();
        let mut seen_non_default: HashSet<usize> = HashSet::new();
        let mut extras: Vec<String> = Vec::new();
        let mut positionals: Vec<usize> = (0..self.actions.len()).filter(|&k| self.actions[k].options.is_empty()).collect();

        let mut start_index = 0usize;
        let max_option = option_indices.keys().copied().max();
        if let Some(max_opt) = max_option {
            while start_index <= max_opt {
                let next_opt = option_indices.keys().copied().filter(|&k| k >= start_index).min().unwrap();
                if start_index != next_opt {
                    let end = self.consume_positionals(start_index, &pattern, arg_strings, &mut positionals, ns, &mut seen, &mut seen_non_default, &conflicts, unrecognized)?;
                    if end > start_index {
                        start_index = end;
                        continue;
                    } else {
                        start_index = end;
                    }
                }
                if !option_indices.contains_key(&start_index) {
                    extras.extend(arg_strings[start_index..next_opt].iter().cloned());
                    start_index = next_opt;
                }
                start_index = self.consume_optional(start_index, &option_indices, &pattern, arg_strings, ns, &mut seen, &mut seen_non_default, &conflicts, &mut extras, unrecognized)?;
            }
        }
        let stop = self.consume_positionals(start_index, &pattern, arg_strings, &mut positionals, ns, &mut seen, &mut seen_non_default, &conflicts, unrecognized)?;
        extras.extend(arg_strings[stop..].iter().cloned());

        let mut required: Vec<String> = Vec::new();
        for (k, a) in self.actions.iter().enumerate() {
            if !seen.contains(&k) {
                if a.required {
                    required.push(action_name(a).unwrap_or_default());
                } else if let (Some(Value::Str(d)), Some(Ty::Int | Ty::Float)) = (&a.default, a.ty) {
                    // a string default is converted, once, when the argument was not given
                    if ns.get(&a.dest).map_or(false, |v| v.as_str() == Some(d.as_str())) {
                        let v = self.get_value(a, d).map_err(Inner::Arg)?;
                        ns.insert(a.dest.clone(), v);
                    }
                }
            }
        }
        if !required.is_empty() {
            return Err(Inner::Arg(format!("the following arguments are required: {}", required.join(", "))));
        }
        for m in &self.mutex {
            if m.required && !m.actions.iter().any(|a| seen_non_default.contains(a)) {
                let names: Vec<String> = m.actions.iter().filter(|&&a| !self.actions[a].help_suppressed()).map(|&a| action_name(&self.actions[a]).unwrap_or_default()).collect();
                return Err(Inner::Arg(format!("one of the arguments {} is required", names.join(" "))));
            }
        }
        Ok(extras)
    }

    #[allow(clippy::too_many_arguments)]
    fn take_action(
        &self,
        k: usize,
        args: Vec<String>,
        option_string: Option<&str>,
        ns: &mut Namespace,
        seen: &mut HashSet<usize>,
        seen_non_default: &mut HashSet<usize>,
        conflicts: &HashMap<usize, Vec<usize>>,
        unrecognized: &mut Vec<String>,
    ) -> Result<(), Inner> {
        let a = &self.actions[k];
        seen.insert(k);
        let (value, is_default) = self.get_values(a, args).map_err(Inner::Arg)?;
        if !is_default {
            seen_non_default.insert(k);
            if let Some(cs) = conflicts.get(&k) {
                for c in cs {
                    if seen_non_default.contains(c) {
                        return Err(Inner::Arg(arg_error(a, format!("not allowed with argument {}", action_name(&self.actions[*c]).unwrap_or_default()))));
                    }
                }
            }
        }
        if value.as_str() == Some(SUPPRESS) && a.nargs == Nargs::Suppress {
            return Ok(());
        }
        let _ = option_string;
        match a.kind {
            Kind::Store => {
                ns.insert(a.dest.clone(), value);
            }
            Kind::StoreTrue => {
                ns.insert(a.dest.clone(), Value::Bool(true));
            }
            Kind::StoreFalse => {
                ns.insert(a.dest.clone(), Value::Bool(false));
            }
            Kind::StoreConst => {
                ns.insert(a.dest.clone(), a.konst.clone());
            }
            Kind::Append => {
                let mut items = match ns.get(&a.dest) {
                    Some(Value::List(l)) => l.clone(),
                    _ => Vec::new(),
                };
                items.push(value);
                ns.insert(a.dest.clone(), Value::List(items));
            }
            Kind::AppendConst => {
                let mut items = match ns.get(&a.dest) {
                    Some(Value::List(l)) => l.clone(),
                    _ => Vec::new(),
                };
                items.push(a.konst.clone());
                ns.insert(a.dest.clone(), Value::List(items));
            }
            Kind::Count => {
                let n = ns.get(&a.dest).and_then(|v| v.as_i64()).unwrap_or(0);
                ns.insert(a.dest.clone(), Value::Int(n + 1));
            }
            Kind::Help => {
                return Err(Inner::Stop(Stop::Exit(0, self.format_help())));
            }
            Kind::Version => {
                return Err(Inner::Stop(Stop::Exit(0, format!("{}\n", a.version.clone().unwrap_or_default()))));
            }
            Kind::Parsers => {
                let vals = value.as_list().cloned().unwrap_or_default();
                let name = vals[0].as_str().unwrap_or("").to_string();
                if a.dest != SUPPRESS {
                    ns.insert(a.dest.clone(), Value::Str(name.clone()));
                }
                let sub = &a.parsers.iter().find(|(n, _)| *n == name).unwrap().1;
                let rest: Vec<String> = vals[1..].iter().map(|v| v.as_str().unwrap_or("").to_string()).collect();
                let (subns, extra) = sub.parse_known_args(&rest, Namespace::new()).map_err(Inner::Stop)?;
                for (key, v) in subns {
                    ns.insert(key, v);
                }
                unrecognized.extend(extra);
            }
        }
        Ok(())
    }

    #[allow(clippy::too_many_arguments)]
    fn consume_optional(
        &self,
        start_index: usize,
        option_indices: &HashMap<usize, OptTuple>,
        pattern: &str,
        arg_strings: &[String],
        ns: &mut Namespace,
        seen: &mut HashSet<usize>,
        seen_non_default: &mut HashSet<usize>,
        conflicts: &HashMap<usize, Vec<usize>>,
        extras: &mut Vec<String>,
        unrecognized: &mut Vec<String>,
    ) -> Result<usize, Inner> {
        let (mut action, mut option_string, mut sep, mut explicit) = option_indices[&start_index].clone();
        let mut tuples: Vec<(usize, Vec<String>, String)> = Vec::new();
        let stop;
        loop {
            let k = match action {
                None => {
                    extras.push(arg_strings[start_index].clone());
                    return Ok(start_index + 1);
                }
                Some(k) => k,
            };
            let a = &self.actions[k];
            if let Some(ex) = explicit.clone() {
                let count = self.match_argument(a, "A").map_err(Inner::Arg)?;
                let second = option_string.chars().nth(1).unwrap_or('-');
                if count == 0 && second != '-' && !ex.is_empty() {
                    if sep.as_deref().map_or(false, |s| !s.is_empty()) || ex.starts_with('-') {
                        return Err(Inner::Arg(arg_error(a, format!("ignored explicit argument {}", pyjson::repr_str(&ex)))));
                    }
                    tuples.push((k, Vec::new(), option_string.clone()));
                    let first = option_string.chars().next().unwrap();
                    let next_char = ex.chars().next().unwrap();
                    let new_opt = format!("{first}{next_char}");
                    if let Some(&j) = self.option_index.get(&new_opt) {
                        action = Some(j);
                        option_string = new_opt;
                        let rest: String = ex.chars().skip(1).collect();
                        if rest.is_empty() {
                            sep = None;
                            explicit = None;
                        } else if let Some(r) = rest.strip_prefix('=') {
                            sep = Some("=".into());
                            explicit = Some(r.to_string());
                        } else {
                            sep = Some(String::new());
                            explicit = Some(rest);
                        }
                    } else {
                        extras.push(format!("{first}{ex}"));
                        stop = start_index + 1;
                        break;
                    }
                } else if count == 1 {
                    stop = start_index + 1;
                    tuples.push((k, vec![ex], option_string.clone()));
                    break;
                } else {
                    return Err(Inner::Arg(arg_error(a, format!("ignored explicit argument {}", pyjson::repr_str(&ex)))));
                }
            } else {
                let start = start_index + 1;
                let selected = &pattern[start.min(pattern.len())..];
                let count = self.match_argument(a, selected).map_err(Inner::Arg)?;
                stop = start + count;
                tuples.push((k, arg_strings[start..stop].to_vec(), option_string.clone()));
                break;
            }
        }
        for (k, args, opt) in tuples {
            self.take_action(k, args, Some(&opt), ns, seen, seen_non_default, conflicts, unrecognized)?;
        }
        Ok(stop)
    }

    #[allow(clippy::too_many_arguments)]
    fn consume_positionals(
        &self,
        mut start_index: usize,
        pattern: &str,
        arg_strings: &[String],
        positionals: &mut Vec<usize>,
        ns: &mut Namespace,
        seen: &mut HashSet<usize>,
        seen_non_default: &mut HashSet<usize>,
        conflicts: &HashMap<usize, Vec<usize>>,
        unrecognized: &mut Vec<String>,
    ) -> Result<usize, Inner> {
        let selected = &pattern[start_index.min(pattern.len())..];
        let counts = self.match_partial(positionals, selected);
        let taken: Vec<usize> = positionals[..counts.len()].to_vec();
        for (k, count) in taken.iter().zip(counts.iter()) {
            let args = arg_strings[start_index..start_index + count].to_vec();
            start_index += count;
            self.take_action(*k, args, None, ns, seen, seen_non_default, conflicts, unrecognized)?;
        }
        positionals.drain(..counts.len());
        Ok(start_index)
    }

    /// The parser for a subcommand path: ["experiment", "add"].
    pub fn sub(&self, path: &[&str]) -> Option<&Parser> {
        let mut p = self;
        for name in path {
            let a = p.actions.iter().find(|a| a.kind == Kind::Parsers)?;
            p = &a.parsers.iter().find(|(n, _)| n == name)?.1;
        }
        Some(p)
    }

    /// The subcommands of this parser, with their help, in order (the `_choices_actions`).
    pub fn subcommands(&self) -> Vec<(String, Option<String>)> {
        match self.actions.iter().find(|a| a.kind == Kind::Parsers) {
            Some(a) => a.choice_actions.iter().map(|c| (c.dest.clone(), c.help.clone())).collect(),
            None => Vec::new(),
        }
    }
}

enum Inner {
    Arg(String),
    Stop(Stop),
}

// ---------------------------------------------------------------- help and usage

pub fn terminal_width() -> usize {
    if let Ok(c) = std::env::var("COLUMNS") {
        if let Ok(n) = c.trim().parse::<i64>() {
            if n > 0 {
                return n as usize;
            }
        }
    }
    crate::sys::terminal_columns().unwrap_or(80)
}

struct Formatter {
    prog: String,
    indent_increment: usize,
    max_help_position: usize,
    width: usize,
    current_indent: usize,
    action_max_length: usize,
}

fn clen(s: &str) -> usize {
    s.chars().count()
}

impl Formatter {
    fn new(prog: &str) -> Formatter {
        let width = terminal_width() as i64 - 2;
        let width = width.max(0) as usize;
        Formatter {
            prog: prog.to_string(),
            indent_increment: 2,
            max_help_position: 24usize.min((width as i64 - 20).max(4) as usize),
            width,
            current_indent: 0,
            action_max_length: 0,
        }
    }

    fn metavar(&self, a: &Action, default: &str) -> String {
        if let Some(m) = &a.metavar {
            m.clone()
        } else if let Some(ch) = &a.choices {
            format!("{{{}}}", ch.iter().map(pyjson::py_str).collect::<Vec<_>>().join(","))
        } else if a.kind == Kind::Parsers {
            format!("{{{}}}", a.parsers.iter().map(|(n, _)| n.clone()).collect::<Vec<_>>().join(","))
        } else {
            default.to_string()
        }
    }

    fn format_args(&self, a: &Action, default: &str) -> String {
        let m = self.metavar(a, default);
        match &a.nargs {
            Nargs::One => m,
            Nargs::Optional => format!("[{m}]"),
            Nargs::ZeroOrMore => format!("[{m} ...]"),
            Nargs::OneOrMore => format!("{m} [{m} ...]"),
            Nargs::Remainder => "...".into(),
            Nargs::Parser => format!("{m} ..."),
            Nargs::Suppress => String::new(),
            Nargs::Exactly(n) => vec![m; *n].join(" "),
        }
    }

    fn invocation(&self, a: &Action) -> String {
        if a.options.is_empty() {
            return self.metavar(a, &a.dest);
        }
        if a.nargs == Nargs::Exactly(0) {
            return a.options.join(", ");
        }
        let args = self.format_args(a, &a.dest.to_uppercase());
        a.options.iter().map(|o| format!("{o} {args}")).collect::<Vec<_>>().join(", ")
    }

    fn actions_usage(&self, p: &Parser, idx: &[usize]) -> String {
        let actions: Vec<&Action> = idx.iter().map(|&k| &p.actions[k]).collect();
        let mut group_actions: HashSet<usize> = HashSet::new();
        let mut inserts: HashMap<usize, String> = HashMap::new();
        for m in &p.mutex {
            let start = match idx.iter().position(|&k| k == m.actions[0]) {
                Some(s) => s,
                None => continue,
            };
            let end = start + m.actions.len();
            if end <= idx.len() && idx[start..end] == m.actions[..] {
                let mut suppressed = 0;
                for &k in &m.actions {
                    group_actions.insert(k);
                    if p.actions[k].help_suppressed() {
                        suppressed += 1;
                    }
                }
                let exposed = m.actions.len() - suppressed;
                if exposed == 0 {
                    continue;
                }
                if !m.required {
                    inserts.entry(start).and_modify(|s| s.push_str(" [")).or_insert_with(|| "[".into());
                    inserts.entry(end).and_modify(|s| s.push(']')).or_insert_with(|| "]".into());
                } else if exposed > 1 {
                    inserts.entry(start).and_modify(|s| s.push_str(" (")).or_insert_with(|| "(".into());
                    inserts.entry(end).and_modify(|s| s.push(')')).or_insert_with(|| ")".into());
                }
                for i in start + 1..end {
                    inserts.insert(i, "|".into());
                }
            }
        }
        let mut parts: Vec<Option<String>> = Vec::new();
        for (i, a) in actions.iter().enumerate() {
            let k = idx[i];
            if a.help_suppressed() {
                parts.push(None);
                if inserts.get(&i).map(|s| s.as_str()) == Some("|") {
                    inserts.remove(&i);
                } else if inserts.get(&(i + 1)).map(|s| s.as_str()) == Some("|") {
                    inserts.remove(&(i + 1));
                }
            } else if a.options.is_empty() {
                let mut part = self.format_args(a, &a.dest);
                if group_actions.contains(&k) && part.starts_with('[') && part.ends_with(']') {
                    part = part[1..part.len() - 1].to_string();
                }
                parts.push(Some(part));
            } else {
                let opt = &a.options[0];
                let mut part = if a.nargs == Nargs::Exactly(0) { opt.clone() } else { format!("{} {}", opt, self.format_args(a, &a.dest.to_uppercase())) };
                if !a.required && !group_actions.contains(&k) {
                    part = format!("[{part}]");
                }
                parts.push(Some(part));
            }
        }
        let mut keys: Vec<usize> = inserts.keys().copied().collect();
        keys.sort_unstable_by(|a, b| b.cmp(a));
        for i in keys {
            let s = inserts[&i].clone();
            parts.insert(i.min(parts.len()), Some(s));
        }
        let text = parts.into_iter().flatten().collect::<Vec<_>>().join(" ");
        static OPEN: OnceLock<regex::Regex> = OnceLock::new();
        static CLOSE: OnceLock<regex::Regex> = OnceLock::new();
        static EMPTY: OnceLock<regex::Regex> = OnceLock::new();
        let text = OPEN.get_or_init(|| regex::Regex::new(r"([\[(]) ").unwrap()).replace_all(&text, "$1").to_string();
        let text = CLOSE.get_or_init(|| regex::Regex::new(r" ([\])])").unwrap()).replace_all(&text, "$1").to_string();
        let text = EMPTY.get_or_init(|| regex::Regex::new(r"[\[(] *[\])]").unwrap()).replace_all(&text, "").to_string();
        text.trim().to_string()
    }

    fn format_usage(&self, p: &Parser) -> String {
        let prefix = "usage: ";
        let usage = if let Some(u) = &p.usage {
            u.replace("%(prog)s", &self.prog)
        } else if p.actions.is_empty() {
            self.prog.clone()
        } else {
            let optionals: Vec<usize> = (0..p.actions.len()).filter(|&k| !p.actions[k].options.is_empty()).collect();
            let positionals: Vec<usize> = (0..p.actions.len()).filter(|&k| p.actions[k].options.is_empty()).collect();
            let all: Vec<usize> = optionals.iter().chain(positionals.iter()).copied().collect();
            let action_usage = self.actions_usage(p, &all);
            let mut usage = [self.prog.clone(), action_usage].into_iter().filter(|s| !s.is_empty()).collect::<Vec<_>>().join(" ");
            let text_width = self.width as i64 - self.current_indent as i64;
            if (clen(prefix) + clen(&usage)) as i64 > text_width {
                let opt_usage = self.actions_usage(p, &optionals);
                let pos_usage = self.actions_usage(p, &positionals);
                let opt_parts = usage_parts(&opt_usage);
                let pos_parts = usage_parts(&pos_usage);
                let prog = self.prog.clone();
                let get_lines = |parts: &[String], indent: &str, prefix: Option<&str>| -> Vec<String> {
                    let mut lines: Vec<String> = Vec::new();
                    let mut line: Vec<String> = Vec::new();
                    let mut line_len: i64 = match prefix {
                        Some(p) => clen(p) as i64 - 1,
                        None => clen(indent) as i64 - 1,
                    };
                    for part in parts {
                        if line_len + 1 + clen(part) as i64 > text_width && !line.is_empty() {
                            lines.push(format!("{}{}", indent, line.join(" ")));
                            line.clear();
                            line_len = clen(indent) as i64 - 1;
                        }
                        line.push(part.clone());
                        line_len += clen(part) as i64 + 1;
                    }
                    if !line.is_empty() {
                        lines.push(format!("{}{}", indent, line.join(" ")));
                    }
                    if prefix.is_some() && !lines.is_empty() {
                        lines[0] = lines[0].chars().skip(clen(indent)).collect();
                    }
                    lines
                };
                let lines: Vec<String> = if (clen(prefix) + clen(&prog)) as f64 <= 0.75 * text_width as f64 {
                    let indent = " ".repeat(clen(prefix) + clen(&prog) + 1);
                    if !opt_parts.is_empty() {
                        let mut first = vec![prog.clone()];
                        first.extend(opt_parts.iter().cloned());
                        let mut l = get_lines(&first, &indent, Some(prefix));
                        l.extend(get_lines(&pos_parts, &indent, None));
                        l
                    } else if !pos_parts.is_empty() {
                        let mut first = vec![prog.clone()];
                        first.extend(pos_parts.iter().cloned());
                        get_lines(&first, &indent, Some(prefix))
                    } else {
                        vec![prog.clone()]
                    }
                } else {
                    let indent = " ".repeat(clen(prefix));
                    let parts: Vec<String> = opt_parts.iter().chain(pos_parts.iter()).cloned().collect();
                    let mut l = get_lines(&parts, &indent, None);
                    if l.len() > 1 {
                        l = get_lines(&opt_parts, &indent, None);
                        l.extend(get_lines(&pos_parts, &indent, None));
                    }
                    let mut out = vec![prog.clone()];
                    out.extend(l);
                    out
                };
                usage = lines.join("\n");
            }
            usage
        };
        format!("{prefix}{usage}\n\n")
    }

    fn format_text(&self, text: &str) -> String {
        let text = text.replace("%(prog)s", &self.prog);
        let text_width = (self.width as i64 - self.current_indent as i64).max(11) as usize;
        let indent = " ".repeat(self.current_indent);
        let t = collapse_ws(&text);
        let lines = wrap(&t, text_width, &indent, &indent);
        format!("{}\n\n", lines.join("\n"))
    }

    fn help_text(&self, a: &Action) -> String {
        // _expand_help: %(prog)s, %(default)s and the like; rb's help strings use none but %%
        let mut h = a.help.clone().unwrap_or_default();
        h = h.replace("%(prog)s", &self.prog);
        if h.contains("%(default)s") {
            let d = a.default.as_ref().map(pyjson::py_str).unwrap_or_default();
            h = h.replace("%(default)s", &d);
        }
        h.replace("%%", "%")
    }

    fn format_action(&self, a: &Action, sub: Option<&ChoiceAction>) -> String {
        let help_position = (self.action_max_length + 2).min(self.max_help_position);
        let help_width = (self.width as i64 - help_position as i64).max(11) as usize;
        let action_width = help_position as i64 - self.current_indent as i64 - 2;
        let (header_text, help) = match sub {
            Some(c) => (c.metavar.clone(), c.help.clone()),
            None => (self.invocation(a), a.help.clone()),
        };
        let mut parts: Vec<String> = Vec::new();
        let indent_first;
        let has_help = help.as_deref().map_or(false, |h| !h.is_empty());
        let header = if !has_help {
            indent_first = 0;
            format!("{}{}\n", " ".repeat(self.current_indent), header_text)
        } else if clen(&header_text) as i64 <= action_width {
            indent_first = 0;
            let pad = (action_width as usize).saturating_sub(clen(&header_text));
            format!("{}{}{}  ", " ".repeat(self.current_indent), header_text, " ".repeat(pad))
        } else {
            indent_first = help_position;
            format!("{}{}\n", " ".repeat(self.current_indent), header_text)
        };
        parts.push(header.clone());
        if let Some(h) = &help {
            if !h.trim().is_empty() {
                let text = match sub {
                    Some(_) => h.replace("%(prog)s", &self.prog).replace("%%", "%"),
                    None => self.help_text(a),
                };
                if !text.is_empty() {
                    let lines = wrap(&collapse_ws(&text), help_width, "", "");
                    if let Some(first) = lines.first() {
                        parts.push(format!("{}{}\n", " ".repeat(indent_first), first));
                    }
                    for line in lines.iter().skip(1) {
                        parts.push(format!("{}{}\n", " ".repeat(help_position), line));
                    }
                }
            } else if !header.ends_with('\n') {
                parts.push("\n".into());
            }
        } else if !header.ends_with('\n') {
            parts.push("\n".into());
        }
        parts.concat()
    }

    fn format_help(&mut self, p: &Parser) -> String {
        // first pass: the widest invocation, as add_argument measures it
        let mut sections: Vec<(Option<String>, Option<String>, Vec<usize>)> = Vec::new();
        for g in &p.groups {
            sections.push((g.title.clone(), g.description.clone(), g.actions.clone()));
        }
        self.current_indent += self.indent_increment; // start_section
        for (_, _, acts) in &sections {
            for &k in acts {
                let a = &p.actions[k];
                if a.help_suppressed() {
                    continue;
                }
                // add_argument measures every invocation, its subactions' too, at the section's indent
                let mut longest = clen(&self.invocation(a));
                if a.kind == Kind::Parsers {
                    for c in &a.choice_actions {
                        longest = longest.max(clen(&c.metavar));
                    }
                }
                self.action_max_length = self.action_max_length.max(longest + self.current_indent);
            }
        }
        self.current_indent -= self.indent_increment;
        let mut out = String::new();
        out.push_str(&self.format_usage(p));
        if let Some(d) = &p.description {
            out.push_str(&self.format_text(d));
        }
        for (title, desc, acts) in &sections {
            self.current_indent += self.indent_increment;
            let mut items = String::new();
            if let Some(d) = desc {
                items.push_str(&self.format_text(d));
            }
            for &k in acts {
                let a = &p.actions[k];
                if a.help_suppressed() {
                    continue;
                }
                items.push_str(&self.format_action(a, None));
                if a.kind == Kind::Parsers {
                    self.current_indent += self.indent_increment;
                    for c in &a.choice_actions {
                        items.push_str(&self.format_action(a, Some(c)));
                    }
                    self.current_indent -= self.indent_increment;
                }
            }
            self.current_indent -= self.indent_increment;
            if !items.is_empty() {
                let heading = match title {
                    Some(t) if t != SUPPRESS => format!("{}{}:\n", " ".repeat(self.current_indent), t),
                    _ => String::new(),
                };
                out.push_str(&format!("\n{heading}{items}\n"));
            }
        }
        if let Some(e) = &p.epilog {
            out.push_str(&self.format_text(e));
        }
        static LONG: OnceLock<regex::Regex> = OnceLock::new();
        let out = LONG.get_or_init(|| regex::Regex::new(r"\n\n\n+").unwrap()).replace_all(&out, "\n\n").to_string();
        let out = out.trim_matches('\n').to_string();
        if out.is_empty() {
            out
        } else {
            out + "\n"
        }
    }
}

fn usage_parts(text: &str) -> Vec<String> {
    // r'\(.*?\)+(?=\s|$)|\[.*?\]+(?=\s|$)|\S+'
    static R: OnceLock<fancy_regex::Regex> = OnceLock::new();
    let re = R.get_or_init(|| fancy_regex::Regex::new(r"\(.*?\)+(?=\s|$)|\[.*?\]+(?=\s|$)|\S+").unwrap());
    re.find_iter(text).filter_map(|m| m.ok()).map(|m| m.as_str().to_string()).collect()
}

fn collapse_ws(text: &str) -> String {
    // re.sub(r'\s+', ' ', text, flags=re.ASCII).strip()
    let mut out = String::new();
    let mut in_ws = false;
    for c in text.chars() {
        if matches!(c, ' ' | '\t' | '\n' | '\r' | '\x0b' | '\x0c') {
            if !in_ws {
                out.push(' ');
                in_ws = true;
            }
        } else {
            out.push(c);
            in_ws = false;
        }
    }
    crate::rbyaml::py_strip(&out).to_string()
}

// ---------------------------------------------------------------- textwrap.wrap

fn wordsep() -> &'static fancy_regex::Regex {
    static R: OnceLock<fancy_regex::Regex> = OnceLock::new();
    R.get_or_init(|| {
        let ws = r"[\t\n\x0b\x0c\r ]";
        let nws = r"[^\t\n\x0b\x0c\r ]";
        let wp = r#"[\w!"'&.,?]"#;
        let lt = r"[^\d\W]";
        let pat = format!(
            r"({ws}+|(?<={wp})-{{2,}}(?=\w)|{nws}+?(?:-(?:(?<={lt}{{2}}-)|(?<={lt}-{lt}-))(?={lt}-?{lt})|(?={ws}|\z)|(?<={wp})(?=-{{2,}}\w)))"
        );
        fancy_regex::Regex::new(&pat).unwrap()
    })
}

/// re.split with the capturing pattern above: the separators and what lies between them, empties dropped.
fn split_chunks(text: &str) -> Vec<String> {
    let re = wordsep();
    let mut out = Vec::new();
    let mut last = 0;
    let mut pos = 0;
    while pos <= text.len() {
        let m = match re.find_from_pos(text, pos) {
            Ok(Some(m)) => m,
            _ => break,
        };
        if m.end() == m.start() {
            // an empty match: step over one character
            let next = text[m.start()..].chars().next().map_or(text.len() + 1, |c| m.start() + c.len_utf8());
            pos = next;
            continue;
        }
        out.push(text[last..m.start()].to_string());
        out.push(m.as_str().to_string());
        last = m.end();
        pos = m.end();
    }
    out.push(text[last..].to_string());
    out.into_iter().filter(|c| !c.is_empty()).collect()
}

/// textwrap.wrap(text, width, initial_indent, subsequent_indent) with the defaults argparse uses.
pub fn wrap(text: &str, width: usize, initial: &str, subsequent: &str) -> Vec<String> {
    // expand tabs and turn every whitespace character into a space
    let munged: String = text.replace('\t', "        ").chars().map(|c| if matches!(c, '\n' | '\x0b' | '\x0c' | '\r') { ' ' } else { c }).collect();
    let mut chunks: Vec<String> = split_chunks(&munged);
    chunks.reverse();
    let mut lines: Vec<String> = Vec::new();
    while !chunks.is_empty() {
        let mut cur_line: Vec<String> = Vec::new();
        let mut cur_len: usize = 0;
        let indent = if lines.is_empty() { initial } else { subsequent };
        let w = width as i64 - clen(indent) as i64;
        if chunks.last().map_or(false, |c| c.trim().is_empty()) && !lines.is_empty() {
            chunks.pop();
        }
        while let Some(c) = chunks.last() {
            let l = clen(c);
            if (cur_len + l) as i64 <= w {
                cur_len += l;
                cur_line.push(chunks.pop().unwrap());
            } else {
                break;
            }
        }
        if chunks.last().map_or(false, |c| clen(c) as i64 > w) {
            // _handle_long_word
            let space_left = if w < 1 { 1 } else { (w - cur_len as i64).max(0) as usize };
            let chunk: Vec<char> = chunks.last().unwrap().chars().collect();
            let mut end = space_left;
            if chunk.len() > space_left {
                let hyphen = chunk[..space_left.min(chunk.len())].iter().rposition(|c| *c == '-');
                if let Some(h) = hyphen {
                    if h > 0 && chunk[..h].iter().any(|c| *c != '-') {
                        end = h + 1;
                    }
                }
            }
            let end = end.min(chunk.len());
            cur_line.push(chunk[..end].iter().collect());
            let rest: String = chunk[end..].iter().collect();
            *chunks.last_mut().unwrap() = rest;
            if chunks.last().map_or(false, |c| c.is_empty()) {
                // an empty chunk is still a chunk in Python; it is dropped as whitespace on the next line
            }
            cur_len = cur_line.iter().map(|c| clen(c)).sum();
        }
        if cur_line.last().map_or(false, |c| c.trim().is_empty()) {
            cur_len -= clen(cur_line.last().unwrap());
            cur_line.pop();
        }
        let _ = cur_len;
        if !cur_line.is_empty() {
            lines.push(format!("{}{}", indent, cur_line.concat()));
        }
    }
    lines
}

// ---------------------------------------------------------------- the public face

impl Parser {
    pub fn format_usage(&self) -> String {
        let u = Formatter::new(&self.prog).format_usage(self);
        let u = u.trim_matches('\n');
        if u.is_empty() {
            String::new()
        } else {
            format!("{u}\n")
        }
    }
    pub fn format_help(&self) -> String {
        Formatter::new(&self.prog).format_help(self)
    }
}
