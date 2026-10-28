//! The value model: what Python's JSON-able values are (None, bool, int, float, str, list, dict), with dicts that keep
//! insertion order the way Python's do. Equality follows Python's `==`: `1 == 1.0`, `True == 1`, dicts compare
//! without regard to order, and NaN equals nothing.

use indexmap::IndexMap;

pub type Dict = IndexMap<String, Value>;

#[derive(Clone, Debug)]
pub enum Value {
    Null,
    Bool(bool),
    Int(i64),
    /// A Python int outside i64: its decimal digits, '-' first when negative, no leading zeros.
    BigInt(String),
    Float(f64),
    Str(String),
    List(Vec<Value>),
    Dict(Dict),
}

impl Default for Value {
    fn default() -> Self {
        Value::Null
    }
}

impl Value {
    pub fn is_null(&self) -> bool {
        matches!(self, Value::Null)
    }
    pub fn is_bool(&self) -> bool {
        matches!(self, Value::Bool(_))
    }
    pub fn is_str(&self) -> bool {
        matches!(self, Value::Str(_))
    }
    pub fn is_list(&self) -> bool {
        matches!(self, Value::List(_))
    }
    pub fn is_dict(&self) -> bool {
        matches!(self, Value::Dict(_))
    }
    /// Python's isinstance(v, int) and not bool.
    pub fn is_int(&self) -> bool {
        matches!(self, Value::Int(_) | Value::BigInt(_))
    }
    pub fn is_float(&self) -> bool {
        matches!(self, Value::Float(_))
    }
    /// isinstance(v, (int, float)) and not isinstance(v, bool)
    pub fn is_number(&self) -> bool {
        matches!(self, Value::Int(_) | Value::BigInt(_) | Value::Float(_))
    }
    pub fn as_str(&self) -> Option<&str> {
        match self {
            Value::Str(s) => Some(s),
            _ => None,
        }
    }
    pub fn as_bool(&self) -> Option<bool> {
        match self {
            Value::Bool(b) => Some(*b),
            _ => None,
        }
    }
    pub fn as_i64(&self) -> Option<i64> {
        match self {
            Value::Int(i) => Some(*i),
            _ => None,
        }
    }
    /// float(v) for an int or a float (not a bool).
    pub fn as_f64(&self) -> Option<f64> {
        match self {
            Value::Int(i) => Some(*i as f64),
            Value::BigInt(s) => s.parse::<f64>().ok(),
            Value::Float(f) => Some(*f),
            _ => None,
        }
    }
    pub fn as_list(&self) -> Option<&Vec<Value>> {
        match self {
            Value::List(l) => Some(l),
            _ => None,
        }
    }
    pub fn as_list_mut(&mut self) -> Option<&mut Vec<Value>> {
        match self {
            Value::List(l) => Some(l),
            _ => None,
        }
    }
    pub fn as_dict(&self) -> Option<&Dict> {
        match self {
            Value::Dict(d) => Some(d),
            _ => None,
        }
    }
    pub fn as_dict_mut(&mut self) -> Option<&mut Dict> {
        match self {
            Value::Dict(d) => Some(d),
            _ => None,
        }
    }
    /// d.get(key) for a dict; None for anything else.
    pub fn get(&self, key: &str) -> Option<&Value> {
        match self {
            Value::Dict(d) => d.get(key),
            _ => None,
        }
    }
    /// d.get(key) that treats a missing key and a null alike.
    pub fn get_nn(&self, key: &str) -> Option<&Value> {
        match self.get(key) {
            Some(Value::Null) | None => None,
            Some(v) => Some(v),
        }
    }
    pub fn get_str(&self, key: &str) -> Option<&str> {
        self.get(key).and_then(|v| v.as_str())
    }
    /// Python truthiness.
    pub fn truthy(&self) -> bool {
        match self {
            Value::Null => false,
            Value::Bool(b) => *b,
            Value::Int(i) => *i != 0,
            Value::BigInt(_) => true,
            Value::Float(f) => *f != 0.0,
            Value::Str(s) => !s.is_empty(),
            Value::List(l) => !l.is_empty(),
            Value::Dict(d) => !d.is_empty(),
        }
    }
    /// The value's type by a short name: what tells 1 from 1.0 from "1" where a change of type is said.
    pub fn type_name(&self) -> &'static str {
        match self {
            Value::Null => "null",
            Value::Bool(_) => "bool",
            Value::Int(_) | Value::BigInt(_) => "int",
            Value::Float(_) => "float",
            Value::Str(_) => "str",
            Value::List(_) => "list",
            Value::Dict(_) => "dict",
        }
    }
    pub fn str<S: Into<String>>(s: S) -> Value {
        Value::Str(s.into())
    }
    pub fn opt_str<S: Into<String>>(s: Option<S>) -> Value {
        match s {
            Some(s) => Value::Str(s.into()),
            None => Value::Null,
        }
    }
    pub fn opt_f64(x: Option<f64>) -> Value {
        match x {
            Some(x) => Value::Float(x),
            None => Value::Null,
        }
    }
    pub fn opt_i64(x: Option<i64>) -> Value {
        match x {
            Some(x) => Value::Int(x),
            None => Value::Null,
        }
    }
    pub fn list_of_str<I: IntoIterator<Item = S>, S: Into<String>>(items: I) -> Value {
        Value::List(items.into_iter().map(|s| Value::Str(s.into())).collect())
    }
    pub fn empty_dict() -> Value {
        Value::Dict(Dict::new())
    }
    /// The number of items of a str (code points), list or dict, as len() reads it.
    pub fn len(&self) -> usize {
        match self {
            Value::Str(s) => s.chars().count(),
            Value::List(l) => l.len(),
            Value::Dict(d) => d.len(),
            _ => 0,
        }
    }
}

/// A Python number, for comparing across int and float exactly.
enum Num<'a> {
    I(i128),
    Big(&'a str),
    F(f64),
}

fn num(v: &Value) -> Option<Num<'_>> {
    match v {
        Value::Bool(b) => Some(Num::I(*b as i128)),
        Value::Int(i) => Some(Num::I(*i as i128)),
        Value::BigInt(s) => Some(Num::Big(s)),
        Value::Float(f) => Some(Num::F(*f)),
        _ => None,
    }
}

/// Python's int == float: exact, never through a rounded conversion.
fn int_eq_float(i: i128, f: f64) -> bool {
    if !f.is_finite() || f.fract() != 0.0 {
        return false;
    }
    if f.abs() >= 1.7e38 {
        return false;
    }
    (f as i128) == i
}

fn big_eq_float(s: &str, f: f64) -> bool {
    if !f.is_finite() || f.fract() != 0.0 {
        return false;
    }
    // the float's exact integer value, in decimal
    crate::pyfloat::float_int_digits(f) == s
}

fn num_eq(a: &Num, b: &Num) -> bool {
    match (a, b) {
        (Num::I(x), Num::I(y)) => x == y,
        (Num::F(x), Num::F(y)) => x == y,
        (Num::I(x), Num::F(y)) | (Num::F(y), Num::I(x)) => int_eq_float(*x, *y),
        (Num::Big(x), Num::Big(y)) => x == y,
        (Num::Big(_), Num::I(_)) | (Num::I(_), Num::Big(_)) => false,
        (Num::Big(x), Num::F(y)) | (Num::F(y), Num::Big(x)) => big_eq_float(x, *y),
    }
}

impl PartialEq for Value {
    fn eq(&self, other: &Value) -> bool {
        if let (Some(a), Some(b)) = (num(self), num(other)) {
            return num_eq(&a, &b);
        }
        match (self, other) {
            (Value::Null, Value::Null) => true,
            (Value::Str(a), Value::Str(b)) => a == b,
            (Value::List(a), Value::List(b)) => a.len() == b.len() && a.iter().zip(b).all(|(x, y)| x == y),
            (Value::Dict(a), Value::Dict(b)) => a.len() == b.len() && a.iter().all(|(k, v)| b.get(k).map_or(false, |w| v == w)),
            _ => false,
        }
    }
}

impl From<&str> for Value {
    fn from(s: &str) -> Value {
        Value::Str(s.to_string())
    }
}
impl From<String> for Value {
    fn from(s: String) -> Value {
        Value::Str(s)
    }
}
impl From<&String> for Value {
    fn from(s: &String) -> Value {
        Value::Str(s.clone())
    }
}
impl From<bool> for Value {
    fn from(b: bool) -> Value {
        Value::Bool(b)
    }
}
impl From<i64> for Value {
    fn from(i: i64) -> Value {
        Value::Int(i)
    }
}
impl From<i32> for Value {
    fn from(i: i32) -> Value {
        Value::Int(i as i64)
    }
}
impl From<usize> for Value {
    fn from(i: usize) -> Value {
        Value::Int(i as i64)
    }
}
impl From<u64> for Value {
    fn from(i: u64) -> Value {
        if i <= i64::MAX as u64 {
            Value::Int(i as i64)
        } else {
            Value::BigInt(i.to_string())
        }
    }
}
impl From<f64> for Value {
    fn from(f: f64) -> Value {
        Value::Float(f)
    }
}
impl From<Dict> for Value {
    fn from(d: Dict) -> Value {
        Value::Dict(d)
    }
}
impl From<Vec<Value>> for Value {
    fn from(l: Vec<Value>) -> Value {
        Value::List(l)
    }
}
impl<T: Into<Value>> From<Option<T>> for Value {
    fn from(o: Option<T>) -> Value {
        match o {
            Some(v) => v.into(),
            None => Value::Null,
        }
    }
}

/// d.get(k) as text, on a Dict.
pub trait DictExt {
    fn get_str(&self, k: &str) -> Option<&str>;
}

impl DictExt for Dict {
    fn get_str(&self, k: &str) -> Option<&str> {
        self.get(k).and_then(|v| v.as_str())
    }
}

/// A dict literal that keeps the order it is written in: `obj!{"ok" => true, "n" => 3}`.
#[macro_export]
macro_rules! obj {
    () => { $crate::value::Value::Dict($crate::value::Dict::new()) };
    ($($k:expr => $v:expr),+ $(,)?) => {{
        let mut d = $crate::value::Dict::new();
        $( d.insert(($k).to_string(), $crate::value::Value::from($v)); )+
        $crate::value::Value::Dict(d)
    }};
}

/// A dict (not wrapped in Value) literal: `dict!{"a" => 1}`.
#[macro_export]
macro_rules! dict {
    () => { $crate::value::Dict::new() };
    ($($k:expr => $v:expr),+ $(,)?) => {{
        let mut d = $crate::value::Dict::new();
        $( d.insert(($k).to_string(), $crate::value::Value::from($v)); )+
        d
    }};
}

/// A list literal of values: `list![1, "a", true]`.
#[macro_export]
macro_rules! list {
    () => { $crate::value::Value::List(Vec::new()) };
    ($($v:expr),+ $(,)?) => { $crate::value::Value::List(vec![$($crate::value::Value::from($v)),+]) };
}
