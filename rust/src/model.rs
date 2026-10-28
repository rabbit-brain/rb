//! Validation the way pydantic v2 does it for rb's records: the same lax conversions (an int for a float field, "yes"
//! for a bool), the same strictness where the Python model is strict, and the same errors, located the same way and
//! with the same messages as the models in `src/rabbit_brain/record.py` give, which is what `rb doctor` and
//! E_STATE_CORRUPT say.

use crate::value::{Dict, Value};
use indexmap::IndexMap;
use std::collections::HashMap;
use std::sync::Mutex;

#[derive(Debug, Clone, PartialEq)]
pub enum LocStep {
    Key(String),
    Index(usize),
}

pub type Loc = Vec<LocStep>;

#[derive(Debug, Clone)]
pub struct VErr {
    pub loc: Loc,
    pub typ: &'static str,
    pub msg: String,
}

impl VErr {
    /// "variants.0.settings.1.status"
    pub fn loc_text(&self) -> String {
        self.loc
            .iter()
            .map(|s| match s {
                LocStep::Key(k) => k.clone(),
                LocStep::Index(i) => i.to_string(),
            })
            .collect::<Vec<_>>()
            .join(".")
    }
}

#[derive(Debug)]
pub struct FieldMeta {
    pub name: &'static str,
    pub required: bool,
    pub sub: Option<&'static ModelMeta>,
}

#[derive(Debug)]
pub struct ModelMeta {
    pub name: &'static str,
    pub written: bool,
    pub fields: &'static [FieldMeta],
}

impl ModelMeta {
    pub fn field(&self, name: &str) -> Option<&FieldMeta> {
        self.fields.iter().find(|f| f.name == name)
    }
}

pub enum C {
    Pattern(&'static str),
    MinLen(usize),
    MaxLen(usize),
    Ge(f64, &'static str),
    Gt(f64, &'static str),
    Le(f64, &'static str),
    Lt(f64, &'static str),
}

fn at(loc: &Loc, key: &str) -> Loc {
    let mut l = loc.clone();
    l.push(LocStep::Key(key.to_string()));
    l
}

fn push(errs: &mut Vec<VErr>, loc: Loc, typ: &'static str, msg: impl Into<String>) {
    errs.push(VErr { loc, typ, msg: msg.into() });
}

pub fn value_error(loc: &Loc, key: Option<&str>, msg: &str) -> VErr {
    VErr { loc: match key { Some(k) => at(loc, k), None => loc.clone() }, typ: "value_error", msg: format!("Value error, {msg}") }
}

/// A constraint's pattern, compiled once for the life of the process. The same Regex every time: a clone would start a
/// matching cache of its own on every call, which is what reading a thousand records spent most of its time on.
pub fn regex_for(pattern: &str) -> &'static regex::Regex {
    static CACHE: Mutex<Option<HashMap<String, &'static regex::Regex>>> = Mutex::new(None);
    let mut g = CACHE.lock().unwrap();
    let map = g.get_or_insert_with(HashMap::new);
    map.entry(pattern.to_string()).or_insert_with(|| Box::leak(Box::new(regex::Regex::new(pattern).unwrap())))
}

pub fn model_dict<'a>(v: &'a Value, name: &str, loc: &Loc, errs: &mut Vec<VErr>) -> Option<&'a Dict> {
    match v {
        Value::Dict(d) => Some(d),
        _ => {
            push(errs, loc.clone(), "model_type", format!("Input should be a valid dictionary or instance of {name}"));
            None
        }
    }
}

pub fn extra_forbidden(d: &Dict, known: &[&str], loc: &Loc, errs: &mut Vec<VErr>) {
    for k in d.keys() {
        if !known.contains(&k.as_str()) {
            push(errs, at(loc, k), "extra_forbidden", "Extra inputs are not permitted");
        }
    }
}

pub fn extra_allowed(d: &Dict, known: &[&str]) -> Dict {
    d.iter().filter(|(k, _)| !known.contains(&k.as_str())).map(|(k, v)| (k.clone(), v.clone())).collect()
}

fn plural(n: usize, word: &str) -> String {
    if n == 1 {
        word.to_string()
    } else {
        format!("{word}s")
    }
}

fn check_str(s: &str, cons: &[C], l: &Loc, errs: &mut Vec<VErr>) -> bool {
    let n = s.chars().count();
    for c in cons {
        match c {
            C::MinLen(m) if n < *m => {
                push(errs, l.clone(), "string_too_short", format!("String should have at least {} {}", m, plural(*m, "character")));
                return false;
            }
            C::MaxLen(m) if n > *m => {
                push(errs, l.clone(), "string_too_long", format!("String should have at most {} {}", m, plural(*m, "character")));
                return false;
            }
            _ => {}
        }
    }
    for c in cons {
        if let C::Pattern(p) = c {
            if !regex_for(p).is_match(s) {
                push(errs, l.clone(), "string_pattern_mismatch", format!("String should match pattern '{p}'"));
                return false;
            }
        }
    }
    true
}

fn check_num(x: f64, cons: &[C], l: &Loc, errs: &mut Vec<VErr>) -> bool {
    for c in cons {
        let bad = match c {
            C::Ge(v, s) if !(x >= *v) => Some(("greater_than_equal", format!("Input should be greater than or equal to {s}"))),
            C::Gt(v, s) if !(x > *v) => Some(("greater_than", format!("Input should be greater than {s}"))),
            C::Le(v, s) if !(x <= *v) => Some(("less_than_equal", format!("Input should be less than or equal to {s}"))),
            C::Lt(v, s) if !(x < *v) => Some(("less_than", format!("Input should be less than {s}"))),
            _ => None,
        };
        if let Some((t, m)) = bad {
            push(errs, l.clone(), t, m);
            return false;
        }
    }
    true
}

fn check_list_len(n: usize, cons: &[C], l: &Loc, errs: &mut Vec<VErr>) -> bool {
    for c in cons {
        match c {
            C::MinLen(m) if n < *m => {
                push(errs, l.clone(), "too_short", format!("List should have at least {} {} after validation, not {}", m, plural(*m, "item"), n));
                return false;
            }
            C::MaxLen(m) if n > *m => {
                push(errs, l.clone(), "too_long", format!("List should have at most {} {} after validation, not {}", m, plural(*m, "item"), n));
                return false;
            }
            _ => {}
        }
    }
    true
}

// ---------------------------------------------------------------- scalar conversions (lax, as pydantic's python mode)

pub fn lax_str(v: &Value, l: &Loc, errs: &mut Vec<VErr>) -> Option<String> {
    match v {
        Value::Str(s) => Some(s.clone()),
        _ => {
            push(errs, l.clone(), "string_type", "Input should be a valid string");
            None
        }
    }
}

pub fn lax_int(v: &Value, l: &Loc, errs: &mut Vec<VErr>) -> Option<i64> {
    match v {
        Value::Int(i) => Some(*i),
        Value::Bool(b) => Some(*b as i64),
        Value::Float(f) => {
            if !f.is_finite() {
                push(errs, l.clone(), "finite_number", "Input should be a finite number");
                None
            } else if f.fract() != 0.0 {
                push(errs, l.clone(), "int_from_float", "Input should be a valid integer, got a number with a fractional part");
                None
            } else if f.abs() >= 9.223372036854775807e18 {
                push(errs, l.clone(), "int_parsing_size", "Unable to parse input string as an integer, exceeded maximum size");
                None
            } else {
                Some(*f as i64)
            }
        }
        Value::Str(s) => match s.trim().parse::<i64>() {
            Ok(i) => Some(i),
            Err(_) => {
                push(errs, l.clone(), "int_parsing", "Input should be a valid integer, unable to parse string as an integer");
                None
            }
        },
        _ => {
            push(errs, l.clone(), "int_type", "Input should be a valid integer");
            None
        }
    }
}

pub fn lax_float(v: &Value, l: &Loc, errs: &mut Vec<VErr>) -> Option<f64> {
    match v {
        Value::Float(f) => Some(*f),
        Value::Int(i) => Some(*i as f64),
        Value::BigInt(s) => s.parse::<f64>().ok(),
        Value::Bool(b) => Some(if *b { 1.0 } else { 0.0 }),
        Value::Str(s) => match crate::pyfloat::parse_float(s) {
            Some(f) => Some(f),
            None => {
                push(errs, l.clone(), "float_parsing", "Input should be a valid number, unable to parse string as a number");
                None
            }
        },
        _ => {
            push(errs, l.clone(), "float_type", "Input should be a valid number");
            None
        }
    }
}

pub fn lax_bool(v: &Value, l: &Loc, errs: &mut Vec<VErr>) -> Option<bool> {
    let bad = |errs: &mut Vec<VErr>| push(errs, l.clone(), "bool_parsing", "Input should be a valid boolean, unable to interpret input");
    match v {
        Value::Bool(b) => Some(*b),
        Value::Int(0) => Some(false),
        Value::Int(1) => Some(true),
        Value::Float(f) if *f == 0.0 => Some(false),
        Value::Float(f) if *f == 1.0 => Some(true),
        Value::Int(_) | Value::BigInt(_) => {
            bad(errs);
            None
        }
        Value::Str(s) => match s.trim().to_lowercase().as_str() {
            "0" | "off" | "f" | "false" | "n" | "no" => Some(false),
            "1" | "on" | "t" | "true" | "y" | "yes" => Some(true),
            _ => {
                bad(errs);
                None
            }
        },
        _ => {
            push(errs, l.clone(), "bool_type", "Input should be a valid boolean");
            None
        }
    }
}

fn literal_msg(choices: &[&str]) -> String {
    let r: Vec<String> = choices.iter().map(|c| crate::pyjson::repr_str(c)).collect();
    match r.len() {
        1 => format!("Input should be {}", r[0]),
        _ => format!("Input should be {} or {}", r[..r.len() - 1].join(", "), r[r.len() - 1]),
    }
}

// ---------------------------------------------------------------- fields

/// A field's value: Some(value) when it is valid (the default when absent and not required), None when an error was
/// recorded for it.
pub fn f_str(d: &Dict, key: &str, required: bool, default: String, cons: &[C], loc: &Loc, errs: &mut Vec<VErr>) -> Option<String> {
    let l = at(loc, key);
    match d.get(key) {
        None => {
            if required {
                push(errs, l, "missing", "Field required");
                None
            } else {
                Some(default)
            }
        }
        Some(v) => {
            let s = lax_str(v, &l, errs)?;
            if check_str(&s, cons, &l, errs) {
                Some(s)
            } else {
                None
            }
        }
    }
}

pub fn f_opt_str(d: &Dict, key: &str, required: bool, default: Option<String>, cons: &[C], loc: &Loc, errs: &mut Vec<VErr>) -> Option<Option<String>> {
    let l = at(loc, key);
    match d.get(key) {
        None => {
            if required {
                push(errs, l, "missing", "Field required");
                None
            } else {
                Some(default)
            }
        }
        Some(Value::Null) => Some(None),
        Some(v) => {
            let s = lax_str(v, &l, errs)?;
            if check_str(&s, cons, &l, errs) {
                Some(Some(s))
            } else {
                None
            }
        }
    }
}

pub fn f_int(d: &Dict, key: &str, required: bool, default: i64, cons: &[C], loc: &Loc, errs: &mut Vec<VErr>) -> Option<i64> {
    let l = at(loc, key);
    match d.get(key) {
        None => {
            if required {
                push(errs, l, "missing", "Field required");
                None
            } else {
                Some(default)
            }
        }
        Some(v) => {
            let i = lax_int(v, &l, errs)?;
            if check_num(i as f64, cons, &l, errs) {
                Some(i)
            } else {
                None
            }
        }
    }
}

pub fn f_opt_int(d: &Dict, key: &str, required: bool, default: Option<i64>, cons: &[C], loc: &Loc, errs: &mut Vec<VErr>) -> Option<Option<i64>> {
    let l = at(loc, key);
    match d.get(key) {
        None => {
            if required {
                push(errs, l, "missing", "Field required");
                None
            } else {
                Some(default)
            }
        }
        Some(Value::Null) => Some(None),
        Some(v) => {
            let i = lax_int(v, &l, errs)?;
            if check_num(i as f64, cons, &l, errs) {
                Some(Some(i))
            } else {
                None
            }
        }
    }
}

pub fn f_float(d: &Dict, key: &str, required: bool, default: f64, cons: &[C], loc: &Loc, errs: &mut Vec<VErr>) -> Option<f64> {
    let l = at(loc, key);
    match d.get(key) {
        None => {
            if required {
                push(errs, l, "missing", "Field required");
                None
            } else {
                Some(default)
            }
        }
        Some(v) => {
            let x = lax_float(v, &l, errs)?;
            if check_num(x, cons, &l, errs) {
                Some(x)
            } else {
                None
            }
        }
    }
}

pub fn f_opt_float(d: &Dict, key: &str, required: bool, default: Option<f64>, cons: &[C], loc: &Loc, errs: &mut Vec<VErr>) -> Option<Option<f64>> {
    let l = at(loc, key);
    match d.get(key) {
        None => {
            if required {
                push(errs, l, "missing", "Field required");
                None
            } else {
                Some(default)
            }
        }
        Some(Value::Null) => Some(None),
        Some(v) => {
            let x = lax_float(v, &l, errs)?;
            if check_num(x, cons, &l, errs) {
                Some(Some(x))
            } else {
                None
            }
        }
    }
}

pub fn f_bool(d: &Dict, key: &str, required: bool, default: bool, _cons: &[C], loc: &Loc, errs: &mut Vec<VErr>) -> Option<bool> {
    let l = at(loc, key);
    match d.get(key) {
        None => {
            if required {
                push(errs, l, "missing", "Field required");
                None
            } else {
                Some(default)
            }
        }
        Some(v) => lax_bool(v, &l, errs),
    }
}

pub fn f_opt_bool(d: &Dict, key: &str, required: bool, default: Option<bool>, _cons: &[C], loc: &Loc, errs: &mut Vec<VErr>) -> Option<Option<bool>> {
    match d.get(key) {
        None if required => { push(errs, at(loc, key), "missing", "Field required"); None }
        None => Some(default),
        Some(Value::Null) => Some(None),
        Some(v) => lax_bool(v, &at(loc, key), errs).map(Some),
    }
}

pub fn f_lit(d: &Dict, key: &str, required: bool, default: String, choices: &[&str], loc: &Loc, errs: &mut Vec<VErr>) -> Option<String> {
    let l = at(loc, key);
    match d.get(key) {
        None => {
            if required {
                push(errs, l, "missing", "Field required");
                None
            } else {
                Some(default)
            }
        }
        Some(Value::Str(s)) if choices.contains(&s.as_str()) => Some(s.clone()),
        Some(_) => {
            push(errs, l, "literal_error", literal_msg(choices));
            None
        }
    }
}

pub fn f_opt_lit(d: &Dict, key: &str, required: bool, default: Option<String>, choices: &[&str], loc: &Loc, errs: &mut Vec<VErr>) -> Option<Option<String>> {
    match d.get(key) {
        Some(Value::Null) => Some(None),
        None if !required => Some(default),
        _ => f_lit(d, key, required, String::new(), choices, loc, errs).map(Some),
    }
}

pub fn f_any(d: &Dict, key: &str, required: bool, default: Value, _cons: &[C], loc: &Loc, errs: &mut Vec<VErr>) -> Option<Value> {
    match d.get(key) {
        None => {
            if required {
                push(errs, at(loc, key), "missing", "Field required");
                None
            } else {
                Some(default)
            }
        }
        Some(v) => Some(v.clone()),
    }
}

pub fn f_dict_any(d: &Dict, key: &str, required: bool, default: Dict, _cons: &[C], loc: &Loc, errs: &mut Vec<VErr>) -> Option<Dict> {
    let l = at(loc, key);
    match d.get(key) {
        None => {
            if required {
                push(errs, l, "missing", "Field required");
                None
            } else {
                Some(default)
            }
        }
        Some(Value::Dict(x)) => Some(x.clone()),
        Some(_) => {
            push(errs, l, "dict_type", "Input should be a valid dictionary");
            None
        }
    }
}

pub fn f_opt_dict_any(d: &Dict, key: &str, required: bool, default: Option<Dict>, cons: &[C], loc: &Loc, errs: &mut Vec<VErr>) -> Option<Option<Dict>> {
    match d.get(key) {
        Some(Value::Null) => Some(None),
        None if !required => Some(default),
        _ => f_dict_any(d, key, required, Dict::new(), cons, loc, errs).map(Some),
    }
}

pub fn f_dict_float(d: &Dict, key: &str, required: bool, default: IndexMap<String, f64>, _cons: &[C], loc: &Loc, errs: &mut Vec<VErr>) -> Option<IndexMap<String, f64>> {
    let l = at(loc, key);
    match d.get(key) {
        None => {
            if required {
                push(errs, l, "missing", "Field required");
                None
            } else {
                Some(default)
            }
        }
        Some(Value::Dict(x)) => {
            let start = errs.len();
            let mut out = IndexMap::new();
            for (k, v) in x {
                let lk = at(&l, k);
                if let Some(f) = lax_float(v, &lk, errs) {
                    out.insert(k.clone(), f);
                }
            }
            if errs.len() > start {
                None
            } else {
                Some(out)
            }
        }
        Some(_) => {
            push(errs, l, "dict_type", "Input should be a valid dictionary");
            None
        }
    }
}

pub fn f_dict_str(d: &Dict, key: &str, required: bool, default: IndexMap<String, String>, _cons: &[C], loc: &Loc, errs: &mut Vec<VErr>) -> Option<IndexMap<String, String>> {
    let l = at(loc, key);
    match d.get(key) {
        None => {
            if required {
                push(errs, l, "missing", "Field required");
                None
            } else {
                Some(default)
            }
        }
        Some(Value::Dict(x)) => {
            let start = errs.len();
            let mut out = IndexMap::new();
            for (k, v) in x {
                let lk = at(&l, k);
                if let Some(s) = lax_str(v, &lk, errs) {
                    out.insert(k.clone(), s);
                }
            }
            if errs.len() > start {
                None
            } else {
                Some(out)
            }
        }
        Some(_) => {
            push(errs, l, "dict_type", "Input should be a valid dictionary");
            None
        }
    }
}

pub fn f_list_str(d: &Dict, key: &str, required: bool, default: Vec<String>, cons: &[C], loc: &Loc, errs: &mut Vec<VErr>) -> Option<Vec<String>> {
    let l = at(loc, key);
    match d.get(key) {
        None => {
            if required {
                push(errs, l, "missing", "Field required");
                None
            } else {
                Some(default)
            }
        }
        Some(Value::List(items)) => {
            let start = errs.len();
            let mut out = Vec::new();
            for (i, v) in items.iter().enumerate() {
                let mut li = l.clone();
                li.push(LocStep::Index(i));
                if let Some(s) = lax_str(v, &li, errs) {
                    out.push(s);
                }
            }
            if errs.len() > start || !check_list_len(out.len(), cons, &l, errs) {
                None
            } else {
                Some(out)
            }
        }
        Some(_) => {
            push(errs, l, "list_type", "Input should be a valid list");
            None
        }
    }
}

pub fn f_list_dict(d: &Dict, key: &str, required: bool, default: Vec<Dict>, _cons: &[C], loc: &Loc, errs: &mut Vec<VErr>) -> Option<Vec<Dict>> {
    let l = at(loc, key);
    match d.get(key) {
        None => {
            if required {
                push(errs, l, "missing", "Field required");
                None
            } else {
                Some(default)
            }
        }
        Some(Value::List(items)) => {
            let start = errs.len();
            let mut out = Vec::new();
            for (i, v) in items.iter().enumerate() {
                match v {
                    Value::Dict(x) => out.push(x.clone()),
                    _ => {
                        let mut li = l.clone();
                        li.push(LocStep::Index(i));
                        push(errs, li, "dict_type", "Input should be a valid dictionary");
                    }
                }
            }
            if errs.len() > start {
                None
            } else {
                Some(out)
            }
        }
        Some(_) => {
            push(errs, l, "list_type", "Input should be a valid list");
            None
        }
    }
}

type Validate<T> = fn(&Value, &mut Loc, &mut Vec<VErr>) -> Option<T>;

#[allow(clippy::too_many_arguments)]
pub fn f_model<T>(d: &Dict, key: &str, required: bool, default: Option<T>, _cons: &[C], loc: &Loc, errs: &mut Vec<VErr>, validate: Validate<T>) -> Option<T> {
    let mut l = at(loc, key);
    match d.get(key) {
        None => {
            if required {
                push(errs, l, "missing", "Field required");
                None
            } else {
                default
            }
        }
        Some(v) => validate(v, &mut l, errs),
    }
}

#[allow(clippy::too_many_arguments)]
pub fn f_opt_model<T>(d: &Dict, key: &str, required: bool, default: Option<T>, _cons: &[C], loc: &Loc, errs: &mut Vec<VErr>, validate: Validate<T>) -> Option<Option<T>> {
    let mut l = at(loc, key);
    match d.get(key) {
        None => {
            if required {
                push(errs, l, "missing", "Field required");
                None
            } else {
                Some(default)
            }
        }
        Some(Value::Null) => Some(None),
        Some(v) => validate(v, &mut l, errs).map(Some),
    }
}

#[allow(clippy::too_many_arguments)]
pub fn f_list_model<T>(d: &Dict, key: &str, required: bool, default: Vec<T>, cons: &[C], loc: &Loc, errs: &mut Vec<VErr>, validate: Validate<T>) -> Option<Vec<T>> {
    let l = at(loc, key);
    match d.get(key) {
        None => {
            if required {
                push(errs, l, "missing", "Field required");
                None
            } else {
                Some(default)
            }
        }
        Some(Value::List(items)) => {
            let start = errs.len();
            let mut out = Vec::new();
            for (i, v) in items.iter().enumerate() {
                let mut li = l.clone();
                li.push(LocStep::Index(i));
                if let Some(x) = validate(v, &mut li, errs) {
                    out.push(x);
                }
            }
            if errs.len() > start || !check_list_len(out.len(), cons, &l, errs) {
                None
            } else {
                Some(out)
            }
        }
        Some(_) => {
            push(errs, l, "list_type", "Input should be a valid list");
            None
        }
    }
}

const SCALAR_MEMBERS: [(&str, &str, &str); 4] = [
    ("bool", "bool_type", "Input should be a valid boolean"),
    ("int", "int_type", "Input should be a valid integer"),
    ("float", "float_type", "Input should be a valid number"),
    ("str", "string_type", "Input should be a valid string"),
];
const LIST_MEMBER: &str = "list[union[bool,int,float,str]]";

fn scalar(v: &Value) -> bool {
    matches!(v, Value::Bool(_) | Value::Int(_) | Value::BigInt(_) | Value::Float(_) | Value::Str(_))
}

/// Optional[Union[StrictBool, StrictInt, StrictFloat, StrictStr, list[Union[...]]]]: a setting's value.
pub fn f_setting_value(d: &Dict, key: &str, required: bool, default: Value, _cons: &[C], loc: &Loc, errs: &mut Vec<VErr>) -> Option<Value> {
    let l = at(loc, key);
    let v = match d.get(key) {
        None => {
            if required {
                push(errs, l, "missing", "Field required");
                return None;
            }
            return Some(default);
        }
        Some(v) => v,
    };
    if v.is_null() || scalar(v) {
        return Some(v.clone());
    }
    let member_errors = |errs: &mut Vec<VErr>, base: &Loc| {
        for (label, typ, msg) in SCALAR_MEMBERS {
            let mut lm = base.clone();
            lm.push(LocStep::Key(label.into()));
            push(errs, lm, typ, msg);
        }
    };
    match v {
        Value::List(items) => {
            let start = errs.len();
            let mut bad_items: Vec<usize> = Vec::new();
            for (i, x) in items.iter().enumerate() {
                if !scalar(x) {
                    bad_items.push(i);
                }
            }
            if bad_items.is_empty() {
                return Some(v.clone());
            }
            member_errors(errs, &l);
            for i in bad_items {
                let mut li = l.clone();
                li.push(LocStep::Key(LIST_MEMBER.into()));
                li.push(LocStep::Index(i));
                member_errors(errs, &li);
            }
            let _ = start;
            None
        }
        _ => {
            member_errors(errs, &l);
            let mut lm = l.clone();
            lm.push(LocStep::Key(LIST_MEMBER.into()));
            push(errs, lm, "list_type", "Input should be a valid list");
            None
        }
    }
}

/// The problems a validation found, as rb lists them: "loc: msg" (an empty loc is "value" where Python said so).
pub fn problems(errs: &[VErr], empty_loc: &str) -> Vec<String> {
    errs.iter()
        .map(|e| {
            let l = e.loc_text();
            format!("{}: {}", if l.is_empty() { empty_loc.to_string() } else { l }, e.msg)
        })
        .collect()
}
