//! JSON the way Python's `json` module writes and reads it, and `str()`/`repr()` of values: rb's envelopes, log lines,
//! hashes and messages are written with it, byte for byte as Python writes them.

use crate::pyfloat;
use crate::value::{Dict, Value};
use std::fmt::Write as _;

#[derive(Clone, Debug)]
pub struct Dumps {
    pub indent: Option<usize>,
    pub item_sep: &'static str,
    pub key_sep: &'static str,
    pub sort_keys: bool,
    pub ensure_ascii: bool,
    pub allow_nan: bool,
}

impl Default for Dumps {
    /// json.dumps(v): ", " and ": ", ASCII only.
    fn default() -> Self {
        Dumps { indent: None, item_sep: ", ", key_sep: ": ", sort_keys: false, ensure_ascii: true, allow_nan: true }
    }
}

impl Dumps {
    /// json.dumps(v, indent=n): "," between items (the newline does the rest).
    pub fn indent(n: usize) -> Self {
        Dumps { indent: Some(n), item_sep: ",", ..Default::default() }
    }
    /// json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False): the canonical form hashes are taken of.
    pub fn canonical() -> Self {
        Dumps { indent: None, item_sep: ",", key_sep: ":", sort_keys: true, ensure_ascii: false, allow_nan: true }
    }
}

#[derive(Debug)]
pub struct NanError;

/// One line of a JSON Lines file, with its newline: `dumps_with` as rb writes its lines (ensure_ascii off), with each
/// character a reader may split a line at (U+0085, U+2028, U+2029, as Python's `str.splitlines` and rb's own reader do)
/// escaped, so a value that holds one is still one line, and every rb reads it back as it was.
pub fn jsonl_line(v: &Value) -> String {
    let text = dumps_with(v, &Dumps { ensure_ascii: false, ..Default::default() }).unwrap_or_default();
    let mut line = if text.contains(['\u{85}', '\u{2028}', '\u{2029}']) {
        text.replace('\u{85}', "\\u0085").replace('\u{2028}', "\\u2028").replace('\u{2029}', "\\u2029")
    } else {
        text
    };
    line.push('\n');
    line
}

/// json.dumps(v) with Python's defaults.
pub fn dumps(v: &Value) -> String {
    dumps_with(v, &Dumps::default()).unwrap_or_default()
}

pub fn dumps_with(v: &Value, o: &Dumps) -> Result<String, NanError> {
    let mut out = String::new();
    write_value(&mut out, v, o, 0)?;
    Ok(out)
}

pub fn write_str(out: &mut String, s: &str, ensure_ascii: bool) {
    out.push('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c if ensure_ascii && (c as u32) > 0x7e => {
                let cp = c as u32;
                if cp > 0xffff {
                    let v = cp - 0x10000;
                    let hi = 0xd800 | (v >> 10);
                    let lo = 0xdc00 | (v & 0x3ff);
                    let _ = write!(out, "\\u{:04x}\\u{:04x}", hi, lo);
                } else {
                    let _ = write!(out, "\\u{:04x}", cp);
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

fn write_float(out: &mut String, f: f64, o: &Dumps) -> Result<(), NanError> {
    if f.is_nan() {
        if !o.allow_nan {
            return Err(NanError);
        }
        out.push_str("NaN");
    } else if f.is_infinite() {
        if !o.allow_nan {
            return Err(NanError);
        }
        out.push_str(if f > 0.0 { "Infinity" } else { "-Infinity" });
    } else {
        out.push_str(&pyfloat::repr(f));
    }
    Ok(())
}

fn newline(out: &mut String, o: &Dumps, level: usize) {
    if let Some(n) = o.indent {
        out.push('\n');
        for _ in 0..(n * level) {
            out.push(' ');
        }
    }
}

fn write_value(out: &mut String, v: &Value, o: &Dumps, level: usize) -> Result<(), NanError> {
    match v {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Int(i) => {
            let _ = write!(out, "{i}");
        }
        Value::BigInt(s) => out.push_str(s),
        Value::Float(f) => write_float(out, *f, o)?,
        Value::Str(s) => write_str(out, s, o.ensure_ascii),
        Value::List(items) => {
            if items.is_empty() {
                out.push_str("[]");
                return Ok(());
            }
            out.push('[');
            newline(out, o, level + 1);
            for (k, x) in items.iter().enumerate() {
                if k > 0 {
                    out.push_str(o.item_sep);
                    newline(out, o, level + 1);
                }
                write_value(out, x, o, level + 1)?;
            }
            newline(out, o, level);
            out.push(']');
        }
        Value::Dict(d) => {
            if d.is_empty() {
                out.push_str("{}");
                return Ok(());
            }
            out.push('{');
            newline(out, o, level + 1);
            let mut keys: Vec<&String> = d.keys().collect();
            if o.sort_keys {
                keys.sort();
            }
            for (k, key) in keys.iter().enumerate() {
                if k > 0 {
                    out.push_str(o.item_sep);
                    newline(out, o, level + 1);
                }
                write_str(out, key, o.ensure_ascii);
                out.push_str(o.key_sep);
                write_value(out, &d[*key], o, level + 1)?;
            }
            newline(out, o, level);
            out.push('}');
        }
    }
    Ok(())
}

// ---------------------------------------------------------------- reading

#[derive(Debug, Clone)]
pub struct JsonError {
    pub msg: String,
    pub pos: usize, // in characters, as Python counts them
    pub lineno: usize,
    pub colno: usize,
}

impl std::fmt::Display for JsonError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: line {} column {} (char {})", self.msg, self.lineno, self.colno, self.pos)
    }
}

struct Parser<'a> {
    s: &'a str,
    b: &'a [u8],
    i: usize, // byte offset
}

impl<'a> Parser<'a> {
    fn err(&self, msg: &str, at: usize) -> JsonError {
        let before = &self.s[..at.min(self.s.len())];
        let pos = before.chars().count();
        let lineno = before.matches('\n').count() + 1;
        let colno = match before.rfind('\n') {
            Some(k) => before[k + 1..].chars().count() + 1,
            None => pos + 1,
        };
        JsonError { msg: msg.to_string(), pos, lineno, colno }
    }

    fn ws(&mut self) {
        while self.i < self.b.len() && matches!(self.b[self.i], b' ' | b'\t' | b'\n' | b'\r') {
            self.i += 1;
        }
    }

    fn value(&mut self, depth: usize) -> Result<Value, JsonError> {
        if depth > 900 {
            return Err(self.err("Expecting value", self.i));
        }
        if self.i >= self.b.len() {
            return Err(self.err("Expecting value", self.i));
        }
        match self.b[self.i] {
            b'"' => {
                let start = self.i;
                self.i += 1;
                Ok(Value::Str(self.string(start)?))
            }
            b'{' => {
                self.i += 1;
                self.object(depth)
            }
            b'[' => {
                self.i += 1;
                self.array(depth)
            }
            b'n' if self.s[self.i..].starts_with("null") => {
                self.i += 4;
                Ok(Value::Null)
            }
            b't' if self.s[self.i..].starts_with("true") => {
                self.i += 4;
                Ok(Value::Bool(true))
            }
            b'f' if self.s[self.i..].starts_with("false") => {
                self.i += 5;
                Ok(Value::Bool(false))
            }
            b'N' if self.s[self.i..].starts_with("NaN") => {
                self.i += 3;
                Ok(Value::Float(f64::NAN))
            }
            b'I' if self.s[self.i..].starts_with("Infinity") => {
                self.i += 8;
                Ok(Value::Float(f64::INFINITY))
            }
            b'-' if self.s[self.i..].starts_with("-Infinity") => {
                self.i += 9;
                Ok(Value::Float(f64::NEG_INFINITY))
            }
            b'-' | b'0'..=b'9' => self.number(),
            _ => Err(self.err("Expecting value", self.i)),
        }
    }

    fn number(&mut self) -> Result<Value, JsonError> {
        let start = self.i;
        let b = self.b;
        let mut j = self.i;
        let neg = b[j] == b'-';
        if neg {
            j += 1;
        }
        if j >= b.len() || !b[j].is_ascii_digit() {
            return Err(self.err("Expecting value", start));
        }
        if b[j] == b'0' {
            j += 1;
        } else {
            while j < b.len() && b[j].is_ascii_digit() {
                j += 1;
            }
        }
        let int_end = j;
        let mut is_float = false;
        if j + 1 < b.len() && b[j] == b'.' && b[j + 1].is_ascii_digit() {
            j += 2;
            while j < b.len() && b[j].is_ascii_digit() {
                j += 1;
            }
            is_float = true;
        }
        if j < b.len() && (b[j] == b'e' || b[j] == b'E') {
            let mut k = j + 1;
            if k < b.len() && (b[k] == b'+' || b[k] == b'-') {
                k += 1;
            }
            if k < b.len() && b[k].is_ascii_digit() {
                while k < b.len() && b[k].is_ascii_digit() {
                    k += 1;
                }
                j = k;
                is_float = true;
            }
        }
        self.i = j;
        let text = &self.s[start..j];
        if is_float {
            let f: f64 = text.parse().unwrap_or(f64::NAN);
            Ok(Value::Float(f))
        } else {
            let digits = &self.s[if neg { start + 1 } else { start }..int_end];
            Ok(pyfloat::int_value(neg, digits))
        }
    }

    fn string(&mut self, start: usize) -> Result<String, JsonError> {
        let mut out = String::new();
        loop {
            let chunk_start = self.i;
            while self.i < self.b.len() {
                let c = self.b[self.i];
                if c == b'"' || c == b'\\' || c < 0x20 {
                    break;
                }
                self.i += 1;
            }
            out.push_str(&self.s[chunk_start..self.i]);
            if self.i >= self.b.len() {
                return Err(self.err("Unterminated string starting at", start));
            }
            let c = self.b[self.i];
            if c == b'"' {
                self.i += 1;
                return Ok(out);
            }
            if c < 0x20 {
                return Err(self.err("Invalid control character at", self.i));
            }
            // a backslash
            self.i += 1;
            if self.i >= self.b.len() {
                return Err(self.err("Unterminated string starting at", start));
            }
            let e = self.b[self.i];
            match e {
                b'"' => out.push('"'),
                b'\\' => out.push('\\'),
                b'/' => out.push('/'),
                b'b' => out.push('\u{8}'),
                b'f' => out.push('\u{c}'),
                b'n' => out.push('\n'),
                b'r' => out.push('\r'),
                b't' => out.push('\t'),
                b'u' => {
                    let esc_at = self.i;
                    let cp = self.hex4(self.i + 1).ok_or_else(|| self.err("Invalid \\uXXXX escape", esc_at))?;
                    self.i += 4;
                    let mut ch = cp;
                    if (0xd800..=0xdbff).contains(&cp) && self.s[self.i + 1..].starts_with("\\u") {
                        if let Some(lo) = self.hex4(self.i + 3) {
                            if (0xdc00..=0xdfff).contains(&lo) {
                                ch = 0x10000 + (((cp - 0xd800) << 10) | (lo - 0xdc00));
                                self.i += 6;
                            }
                        }
                    }
                    out.push(char::from_u32(ch).unwrap_or('\u{fffd}'));
                }
                _ => return Err(self.err("Invalid \\escape", self.i - 1)),
            }
            self.i += 1;
        }
    }

    fn hex4(&self, at: usize) -> Option<u32> {
        if at + 4 > self.b.len() {
            return None;
        }
        let h = &self.s.get(at..at + 4)?;
        if !h.bytes().all(|c| c.is_ascii_hexdigit()) {
            return None;
        }
        u32::from_str_radix(h, 16).ok()
    }

    fn object(&mut self, depth: usize) -> Result<Value, JsonError> {
        let mut d = Dict::new();
        self.ws();
        if self.i < self.b.len() && self.b[self.i] == b'}' {
            self.i += 1;
            return Ok(Value::Dict(d));
        }
        loop {
            if self.i >= self.b.len() || self.b[self.i] != b'"' {
                return Err(self.err("Expecting property name enclosed in double quotes", self.i));
            }
            let start = self.i;
            self.i += 1;
            let key = self.string(start)?;
            self.ws();
            if self.i >= self.b.len() || self.b[self.i] != b':' {
                return Err(self.err("Expecting ':' delimiter", self.i));
            }
            self.i += 1;
            self.ws();
            let v = self.value(depth + 1)?;
            d.insert(key, v);
            self.ws();
            if self.i < self.b.len() && self.b[self.i] == b'}' {
                self.i += 1;
                return Ok(Value::Dict(d));
            }
            if self.i >= self.b.len() || self.b[self.i] != b',' {
                return Err(self.err("Expecting ',' delimiter", self.i));
            }
            self.i += 1;
            self.ws();
        }
    }

    fn array(&mut self, depth: usize) -> Result<Value, JsonError> {
        let mut out = Vec::new();
        self.ws();
        if self.i < self.b.len() && self.b[self.i] == b']' {
            self.i += 1;
            return Ok(Value::List(out));
        }
        loop {
            out.push(self.value(depth + 1)?);
            self.ws();
            if self.i < self.b.len() && self.b[self.i] == b']' {
                self.i += 1;
                return Ok(Value::List(out));
            }
            if self.i >= self.b.len() || self.b[self.i] != b',' {
                return Err(self.err("Expecting ',' delimiter", self.i));
            }
            self.i += 1;
            self.ws();
        }
    }
}

/// json.loads(text).
pub fn loads(text: &str) -> Result<Value, JsonError> {
    let mut p = Parser { s: text, b: text.as_bytes(), i: 0 };
    if text.starts_with('\u{feff}') {
        return Err(JsonError { msg: "Unexpected UTF-8 BOM (decode using utf-8-sig)".into(), pos: 0, lineno: 1, colno: 1 });
    }
    p.ws();
    let v = p.value(0)?;
    p.ws();
    if p.i != p.b.len() {
        return Err(p.err("Extra data", p.i));
    }
    Ok(v)
}

// ---------------------------------------------------------------- str() and repr() of values

fn printable(c: char) -> bool {
    let cp = c as u32;
    if c == ' ' {
        return true;
    }
    if cp < 0x20 || (0x7f..=0xa0).contains(&cp) {
        return false;
    }
    if c.is_whitespace() {
        return false; // the Unicode separators: not printable to Python
    }
    cp != 0xad
        && !matches!(cp, 0x600..=0x605 | 0x61c | 0x6dd | 0x70f | 0x180e | 0x200b..=0x200f | 0x202a..=0x202e | 0x2060..=0x2064
            | 0x2066..=0x206f | 0xfeff | 0xfff9..=0xfffb | 0xe000..=0xf8ff | 0xf0000..=0x10ffff | 0xfffe | 0xffff)
}

/// repr(s) for a str.
pub fn repr_str(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') { '"' } else { '\'' };
    let mut out = String::new();
    out.push(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if printable(c) => out.push(c),
            c => {
                let cp = c as u32;
                if cp < 0x100 {
                    let _ = write!(out, "\\x{:02x}", cp);
                } else if cp < 0x10000 {
                    let _ = write!(out, "\\u{:04x}", cp);
                } else {
                    let _ = write!(out, "\\U{:08x}", cp);
                }
            }
        }
    }
    out.push(quote);
    out
}

/// repr(v): what Python prints for {v!r}.
pub fn py_repr(v: &Value) -> String {
    match v {
        Value::Str(s) => repr_str(s),
        other => py_str(other),
    }
}

/// str(v): what Python prints for {v}.
pub fn py_str(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Int(i) => i.to_string(),
        Value::BigInt(s) => s.clone(),
        Value::Float(f) => pyfloat::repr(*f),
        Value::Str(s) => s.clone(),
        Value::List(items) => {
            let parts: Vec<String> = items.iter().map(py_repr).collect();
            format!("[{}]", parts.join(", "))
        }
        Value::Dict(d) => {
            let parts: Vec<String> = d.iter().map(|(k, v)| format!("{}: {}", repr_str(k), py_repr(v))).collect();
            format!("{{{}}}", parts.join(", "))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip() {
        let v = loads(r#"{"a": [1, 2.5, -0.0, 1e-05, "xé😀"], "b": null, "c": true, "big": 123456789012345678901234567890}"#).unwrap();
        let esc = format!("x{0}u00e9{0}ud83d{0}ude00", "\\");
        assert_eq!(dumps(&v), format!("{{\"a\": [1, 2.5, -0.0, 1e-05, \"{esc}\"], \"b\": null, \"c\": true, \"big\": 123456789012345678901234567890}}"));
        assert_eq!(
            dumps_with(&v, &Dumps::canonical()).unwrap(),
            "{\"a\":[1,2.5,-0.0,1e-05,\"xé😀\"],\"b\":null,\"big\":123456789012345678901234567890,\"c\":true}"
        );
    }

    #[test]
    fn errors() {
        let e = loads("{\"a\": }").unwrap_err();
        assert_eq!(e.to_string(), "Expecting value: line 1 column 7 (char 6)");
        let e = loads("[1, 2] x").unwrap_err();
        assert_eq!(e.to_string(), "Extra data: line 1 column 8 (char 7)");
    }

    #[test]
    fn reprs() {
        assert_eq!(py_repr(&Value::Str("it's".into())), "\"it's\"");
        assert_eq!(py_repr(&Value::Str("a\nb".into())), "'a\\nb'");
        let v = loads(r#"{"a": [1, "b", null, true]}"#).unwrap();
        assert_eq!(py_str(&v), "{'a': [1, 'b', None, True]}");
    }

    #[test]
    fn indent() {
        let v = loads(r#"{"a": [1, {}], "b": {"c": []}}"#).unwrap();
        assert_eq!(dumps_with(&v, &Dumps::indent(1)).unwrap(), "{\n \"a\": [\n  1,\n  {}\n ],\n \"b\": {\n  \"c\": []\n }\n}");
    }
}
