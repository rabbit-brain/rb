//! Rabbit Brain YAML: the YAML every record is written in, and the only YAML rb reads as a record: mappings, lists,
//! text, numbers, true/false, null and comments; nothing else. A plain value YAML 1.1 and 1.2 parsers read differently
//! is refused with the spelling to write instead. `dumps` writes the canonical form (`rb fmt`), putting back the
//! comments `load` anchored to keys and list items. `canonical_json` is the machine form every content hash is taken
//! of. `tests/rbyaml.py` is the reference reader it is held to.

use crate::pyfloat;
use crate::pyjson::{self, Dumps};
use crate::value::{Dict, Value};
use indexmap::{IndexMap, IndexSet};
use regex::Regex;
use sha2::{Digest, Sha256};
use std::sync::OnceLock;

pub const MAX_DEPTH: usize = 64;
const INDICATORS: &str = "-?:,[]{}#&*!|>'\"%@`";

#[derive(Debug, Clone)]
pub struct YAMLError {
    pub message: String,
    pub line: usize,
    pub col: usize,
    pub fix: String,
    pub source: String,
}

impl std::fmt::Display for YAMLError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let where_ = if self.line > 0 {
            format!("{}:{}:{}: ", if self.source.is_empty() { "<text>" } else { &self.source }, self.line, self.col)
        } else if !self.source.is_empty() {
            format!("{}: ", self.source)
        } else {
            String::new()
        };
        write!(f, "{}{}", where_, self.message)?;
        if !self.fix.is_empty() {
            write!(f, "; {}", self.fix)?;
        }
        Ok(())
    }
}

struct Ambiguous {
    message: String,
    fix: String,
}

/// One step of a comment's path: a key, a list position, or a list item named by its `id` or `name`.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum Step {
    Key(String),
    Index(usize),
    Named(&'static str, String),
}

pub type CPath = Vec<Step>;

#[derive(Debug, Clone, Default)]
pub struct Comments {
    pub before: IndexMap<CPath, Vec<String>>,
    pub after: IndexMap<CPath, String>,
    pub footer: Vec<String>,
}

impl Comments {
    pub fn is_empty(&self) -> bool {
        self.before.is_empty() && self.after.is_empty() && self.footer.is_empty()
    }
}

#[derive(Debug, Clone)]
pub struct Document {
    pub data: Value,
    pub comments: Comments,
}

// ---------------------------------------------------------------- regexes and character classes

fn re(cell: &'static OnceLock<Regex>, pat: &str) -> &'static Regex {
    cell.get_or_init(|| Regex::new(pat).unwrap())
}

fn re_int() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    re(&R, r"^[-+]?(?:0|[1-9][0-9]*)$")
}
fn re_float() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    re(&R, r"^(?:[-+]?[0-9]+\.[0-9]*(?:[eE][-+][0-9]+)?|\.[0-9]+(?:[eE][-+][0-9]+)?)$")
}
fn re_numeric() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    re(
        &R,
        r"(?x)^(?:[-+]?[0-9][0-9_]*
    |[-+]?0[bBoOxX][0-9a-fA-F_]+
    |[-+]?(?:[0-9][0-9_]*\.[0-9_]*|\.[0-9][0-9_]*)(?:[eE][-+]?[0-9]+)?
    |[-+]?[0-9][0-9_]*[eE][-+]?[0-9]+
    |[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+(?:\.[0-9_]*)?
    |[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$",
    )
}
fn re_timestamp() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    re(
        &R,
        r"^[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}(?:(?:[Tt]|[ \t]+)[0-9]{1,2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]*)?(?:[ \t]*(?:Z|[-+][0-9]{1,2}(?::[0-9]{2})?))?)?$",
    )
}
fn re_date_prefix() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    re(&R, r"^[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}")
}

/// A character a YAML file cannot hold as it is.
pub fn is_bad(c: char) -> bool {
    let o = c as u32;
    matches!(o, 0x00..=0x08 | 0x0b | 0x0c | 0x0e..=0x1f | 0x7f..=0x9f | 0x2028 | 0x2029 | 0xfeff | 0xfffe | 0xffff | 0xd800..=0xdfff)
}

/// Python's str.isspace() for one character.
pub fn py_isspace(c: char) -> bool {
    matches!(c as u32, 0x09..=0x0d | 0x1c..=0x20 | 0x85 | 0xa0 | 0x1680 | 0x2000..=0x200a | 0x2028 | 0x2029 | 0x202f | 0x205f | 0x3000)
}

// ---------------------------------------------------------------- char-slice helpers (Python str semantics)

type Chars = Vec<char>;

fn s_of(c: &[char]) -> String {
    c.iter().collect()
}

fn strip_set<'a>(c: &'a [char], set: &str) -> &'a [char] {
    let mut a = 0;
    let mut b = c.len();
    while a < b && set.contains(c[a]) {
        a += 1;
    }
    while b > a && set.contains(c[b - 1]) {
        b -= 1;
    }
    &c[a..b]
}

fn lstrip_set<'a>(c: &'a [char], set: &str) -> &'a [char] {
    let mut a = 0;
    while a < c.len() && set.contains(c[a]) {
        a += 1;
    }
    &c[a..]
}

fn rstrip_set<'a>(c: &'a [char], set: &str) -> &'a [char] {
    let mut b = c.len();
    while b > 0 && set.contains(c[b - 1]) {
        b -= 1;
    }
    &c[..b]
}

/// str.rstrip() with no argument.
fn rstrip_ws(c: &[char]) -> &[char] {
    let mut b = c.len();
    while b > 0 && py_isspace(c[b - 1]) {
        b -= 1;
    }
    &c[..b]
}

/// str.strip() with no argument.
pub fn py_strip(s: &str) -> &str {
    s.trim_matches(py_isspace)
}

/// str.rstrip() with no argument.
pub fn py_rstrip(s: &str) -> &str {
    s.trim_end_matches(py_isspace)
}

fn starts(c: &[char], p: &str) -> bool {
    let mut i = 0;
    for ch in p.chars() {
        if i >= c.len() || c[i] != ch {
            return false;
        }
        i += 1;
    }
    true
}

fn contains_seq(c: &[char], p: &str) -> bool {
    let n = p.chars().count();
    n == 0 || (n <= c.len() && c.windows(n).any(|w| w.iter().copied().eq(p.chars())))
}

fn is_dash(text: &[char]) -> bool {
    (text.len() == 1 && text[0] == '-') || starts(text, "- ")
}

/// The index of the first " #" or "\t#" in c[..end] (the whole match inside), or None.
fn comment_at(c: &[char], end: usize) -> Option<usize> {
    let end = end.min(c.len());
    if end < 2 {
        return None;
    }
    (0..end - 1).find(|&p| (c[p] == ' ' || c[p] == '\t') && c[p + 1] == '#')
}

fn cut_comment(s: &[char]) -> (Chars, Option<String>) {
    match comment_at(s, s.len()) {
        None => (s.to_vec(), None),
        Some(m) => (s[..m].to_vec(), Some(s_of(rstrip_ws(&s[m + 1..])))),
    }
}

/// Bytes that are not UTF-8, and where they stop being it: "not UTF-8 text (the byte 0xff at offset 20)". Rabbit
/// Brain YAML is UTF-8, so every reader of a record file says this the same way.
pub fn not_utf8(bytes: &[u8], e: &std::str::Utf8Error) -> String {
    let at = e.valid_up_to();
    match e.error_len() {
        None => format!("not UTF-8 text (it stops partway through a character, at offset {at})"),
        Some(_) => format!("not UTF-8 text (the byte 0x{:02x} at offset {at})", bytes[at]),
    }
}

// ---------------------------------------------------------------- reading a plain value

const NULLS: &[&str] = &["", "~", "null", "Null", "NULL"];
const TRUES: &[&str] = &["true", "True", "TRUE"];
const FALSES: &[&str] = &["false", "False", "FALSE"];
const BOOL11: &[&str] = &["yes", "Yes", "YES", "no", "No", "NO", "on", "On", "ON", "off", "Off", "OFF", "y", "Y", "n", "N"];

fn resolve(s: &str) -> Result<Value, Ambiguous> {
    if NULLS.contains(&s) {
        return Ok(Value::Null);
    }
    if TRUES.contains(&s) {
        return Ok(Value::Bool(true));
    }
    if FALSES.contains(&s) {
        return Ok(Value::Bool(false));
    }
    let c = s.chars().next().unwrap();
    if "0123456789+-.".contains(c) {
        if re_int().is_match(s) {
            let (neg, digits) = match s.strip_prefix('-') {
                Some(d) => (true, d),
                None => (false, s.strip_prefix('+').unwrap_or(s)),
            };
            return Ok(pyfloat::int_value(neg, digits));
        }
        if re_float().is_match(s) {
            // a number too large to hold reads as infinity, which Rabbit Brain YAML does not have: refused here, by name
            let x = s.parse::<f64>().unwrap_or(f64::NAN);
            if !x.is_finite() {
                return Err(Ambiguous {
                    message: format!("{s} is too large to keep as a number"),
                    fix: format!("write a number up to about 1.8e+308, or quote it for text: \"{s}\""),
                });
            }
            return Ok(Value::Float(x));
        }
        if re_numeric().is_match(s) {
            return Err(Ambiguous {
                message: format!("{s} is a number to some YAML parsers and not to others"),
                fix: format!("write it as a plain decimal number (7, 0.5, 1.0e+3), or quote it for text: \"{s}\""),
            });
        }
        if re_timestamp().is_match(s) {
            return Err(Ambiguous { message: format!("{s} is a date to some YAML parsers and text to others"), fix: format!("quote it: \"{s}\"") });
        }
    } else if BOOL11.contains(&s) {
        return Err(Ambiguous {
            message: format!("{s} is true or false to some YAML parsers and text to others"),
            fix: format!("write true or false, or quote it for text: \"{s}\""),
        });
    } else if s == "<<" || s == "=" {
        return Err(Ambiguous { message: format!("{s} has a special meaning to some YAML parsers"), fix: format!("quote it: \"{s}\"") });
    }
    Ok(Value::Str(s.to_string()))
}

/// How the items of a list are named in a comment's path: by `id`, by `name`, or (None) by position.
pub fn item_scheme(items: &[Value]) -> Option<&'static str> {
    for key in ["id", "name"] {
        let vals: Vec<Option<&str>> = items.iter().map(|x| x.get(key).and_then(|v| v.as_str())).collect();
        if !vals.is_empty() && vals.iter().all(|v| v.is_some()) {
            let set: std::collections::HashSet<&str> = vals.iter().map(|v| v.unwrap()).collect();
            if set.len() == vals.len() {
                return Some(key);
            }
        }
    }
    None
}

// ---------------------------------------------------------------- the parser

struct Parser {
    source: String,
    lines: Vec<Chars>,
    n: usize,
    i: usize,
    comments: Comments,
    pending: Vec<String>,
    started: bool,
    depth: usize,
}

type R<T> = Result<T, YAMLError>;

impl Parser {
    fn new(text: &str, source: &str) -> R<Parser> {
        let text = text.strip_prefix('\u{feff}').unwrap_or(text);
        let text = text.replace("\r\n", "\n").replace('\r', "\n");
        if let Some((bpos, bad)) = text.char_indices().find(|(_, c)| is_bad(*c)) {
            let before = &text[..bpos];
            let line = before.matches('\n').count() + 1;
            let col = match before.rfind('\n') {
                Some(k) => before[k + 1..].chars().count() + 1,
                None => before.chars().count() + 1,
            };
            let o = bad as u32;
            return Err(YAMLError {
                message: format!("character U+{o:04X} cannot stand as it is in a YAML file"),
                line,
                col,
                fix: format!("write it as an escape inside double quotes (\"\\u{o:04x}\")"),
                source: source.to_string(),
            });
        }
        let mut lines: Vec<Chars> = text.split('\n').map(|l| l.chars().collect()).collect();
        if lines.last().map_or(false, |l| l.is_empty()) {
            lines.pop();
        }
        let n = lines.len();
        Ok(Parser { source: source.to_string(), lines, n, i: 0, comments: Comments::default(), pending: Vec::new(), started: false, depth: 0 })
    }

    fn err(&self, message: impl Into<String>, line: usize, col: usize, fix: impl Into<String>) -> YAMLError {
        YAMLError { message: message.into(), line, col, fix: fix.into(), source: self.source.clone() }
    }

    fn enter(&mut self) -> R<()> {
        self.depth += 1;
        if self.depth > MAX_DEPTH {
            return Err(self.err(format!("nested more than {MAX_DEPTH} levels deep"), self.i + 1, 1, ""));
        }
        Ok(())
    }

    fn indent(&self, k: usize) -> R<usize> {
        let line = &self.lines[k];
        let n = line.len() - lstrip_set(line, " ").len();
        if n < line.len() && line[n] == '\t' {
            return Err(self.err("a tab is not indentation in YAML", k + 1, n + 1, "indent with spaces"));
        }
        Ok(n)
    }

    fn blank(&self, k: usize) -> bool {
        strip_set(&self.lines[k], " \t").is_empty()
    }

    fn is_start(&self, k: usize) -> bool {
        let l = &self.lines[k];
        (l.len() == 3 && starts(l, "---")) || starts(l, "--- ") || starts(l, "---\t")
    }

    fn is_end(&self, k: usize) -> bool {
        let l = &self.lines[k];
        (l.len() == 3 && starts(l, "...")) || starts(l, "... ") || starts(l, "...\t")
    }

    fn is_marker(&self, k: usize) -> bool {
        self.is_start(k) || self.is_end(k)
    }

    fn skip(&mut self) {
        while self.i < self.n {
            let s = strip_set(&self.lines[self.i], " \t");
            if s.is_empty() {
                if (!self.pending.is_empty() || self.started) && (self.pending.is_empty() || self.pending.last().map_or(false, |x| !x.is_empty())) {
                    self.pending.push(String::new());
                }
            } else if s[0] == '#' {
                let c = s_of(rstrip_ws(s));
                self.pending.push(c);
            } else {
                return;
            }
            self.i += 1;
        }
    }

    fn attach(&mut self, path: &CPath) {
        self.started = true;
        if !self.pending.is_empty() {
            let p = std::mem::take(&mut self.pending);
            self.comments.before.entry(path.clone()).or_default().extend(p);
        }
    }

    fn document(&mut self) -> R<Value> {
        self.skip();
        if self.i < self.n && starts(&self.lines[self.i], "%") {
            return Err(self.err("directives (% lines) are not part of Rabbit Brain YAML", self.i + 1, 1, "remove the line"));
        }
        if self.i < self.n && self.is_start(self.i) {
            let rest = s_of(strip_set(&self.lines[self.i][3..], " \t"));
            if !rest.is_empty() && !rest.starts_with('#') {
                return Err(self.err("the document starts on the line after ---", self.i + 1, 5, "move it down a line"));
            }
            if !rest.is_empty() {
                self.pending.push(rest);
            }
            self.i += 1;
        }
        let data = self.node(-1, &Vec::new())?;
        self.skip();
        if self.i < self.n {
            if self.is_end(self.i) {
                let rest = s_of(strip_set(&self.lines[self.i][3..], " \t"));
                if !rest.is_empty() && !rest.starts_with('#') {
                    return Err(self.err("nothing may follow ... on its line", self.i + 1, 5, ""));
                }
                self.i += 1;
                self.skip();
                if self.i < self.n {
                    return Err(self.err("one document per file: nothing may follow ...", self.i + 1, 1, "put the second document in a file of its own"));
                }
            } else if self.is_start(self.i) {
                return Err(self.err("one document per file", self.i + 1, 1, "put the second document in a file of its own"));
            } else {
                let k = self.i;
                let ind = self.indent(k)?;
                return Err(self.err("this line does not fit the structure above it", k + 1, ind + 1, "check its indentation against the lines above"));
            }
        }
        let mut footer: Vec<String> = self.pending.clone();
        while footer.last().map_or(false, |x| x.is_empty()) {
            footer.pop();
        }
        while footer.first().map_or(false, |x| x.is_empty()) {
            footer.remove(0);
        }
        self.comments.footer = footer;
        self.comments = identify(&self.comments, &data);
        Ok(data)
    }

    fn node(&mut self, parent: i64, path: &CPath) -> R<Value> {
        self.skip();
        if self.i >= self.n || self.is_marker(self.i) {
            return Ok(Value::Null);
        }
        let ind = self.indent(self.i)?;
        if (ind as i64) <= parent {
            return Ok(Value::Null);
        }
        let (dash, keyed) = {
            let text = &self.lines[self.i][ind..];
            (is_dash(text), !is_dash(text) && self.key_end(text).is_some())
        };
        if dash {
            return self.seq(ind, path);
        }
        if keyed {
            return self.map(ind, path);
        }
        self.attach(path);
        let i = self.i;
        self.inline(i, ind, parent, path)
    }

    /// The index of the ':' that makes `text` a `key: value` line, or None.
    fn key_end(&self, text: &[char]) -> Option<usize> {
        if text.is_empty() || "[{|>#".contains(text[0]) {
            return None;
        }
        if text[0] == '"' || text[0] == '\'' {
            let end = quoted_end(text)?;
            let mut j = end;
            while j < text.len() && (text[j] == ' ' || text[j] == '\t') {
                j += 1;
            }
            if j < text.len() && text[j] == ':' && (j + 1 == text.len() || text[j + 1] == ' ' || text[j + 1] == '\t') {
                return Some(j);
            }
            return None;
        }
        let mut j = 0;
        loop {
            let found = (j..text.len()).find(|&p| text[p] == ':')?;
            j = found;
            if j + 1 == text.len() || text[j + 1] == ' ' || text[j + 1] == '\t' {
                return if comment_at(text, j).is_some() { None } else { Some(j) };
            }
            j += 1;
        }
    }

    fn key_text(&self, raw: &[char], k: usize, col: usize) -> R<String> {
        if !raw.is_empty() && (raw[0] == '"' || raw[0] == '\'') {
            let (value, end) = quoted_line(raw).map_err(|(m, c)| self.err(m, k + 1, col + c + 1, ""))?;
            if !strip_set(&raw[end..], " \t").is_empty() {
                return Err(self.err("unexpected text after a quoted key", k + 1, col + end + 1, ""));
            }
            return Ok(value);
        }
        if raw.is_empty() {
            return Err(self.err("a key is missing before ':'", k + 1, col + 1, "write the key, or quote the text if ':' is part of it"));
        }
        let rs = s_of(raw);
        if "&*!".contains(raw[0]) {
            let what = match raw[0] {
                '&' => "anchors",
                '*' => "aliases",
                _ => "tags",
            };
            return Err(self.err(format!("{what} are not part of Rabbit Brain YAML"), k + 1, col + 1, "write the key as plain text"));
        }
        if INDICATORS.contains(raw[0]) && !("-?:".contains(raw[0]) && raw.len() > 1 && raw[1] != ' ' && raw[1] != '\t') {
            return Err(self.err(
                format!("a key cannot start with {} unquoted", pyjson::repr_str(&raw[0].to_string())),
                k + 1,
                col + 1,
                format!("quote it: \"{rs}\""),
            ));
        }
        let value = resolve(&rs).map_err(|a| self.err(a.message, k + 1, col + 1, a.fix))?;
        match value {
            Value::Str(s) => Ok(s),
            other => Err(self.err(format!("a key is text, and {rs} reads as {}", pyjson::dumps(&other)), k + 1, col + 1, format!("quote it: \"{rs}\""))),
        }
    }

    fn map(&mut self, ind: usize, path: &CPath) -> R<Value> {
        self.enter()?;
        let mut out = Dict::new();
        loop {
            self.skip();
            if self.i >= self.n || self.is_marker(self.i) {
                break;
            }
            let k = self.i;
            let li = self.indent(k)?;
            if li < ind {
                break;
            }
            if li > ind {
                return Err(self.err(
                    "this line is indented more than the keys beside it",
                    k + 1,
                    li + 1,
                    "line it up with its sibling keys, or put it under a key that ends with ':'",
                ));
            }
            let text = &self.lines[k][ind..];
            if is_dash(text) {
                return Err(self.err("a list item where a key was expected", k + 1, ind + 1, "indent the list under the key it belongs to"));
            }
            if (text.len() == 1 && text[0] == '?') || starts(text, "? ") || starts(text, "?\t") {
                return Err(self.err("complex keys (? ) are not part of Rabbit Brain YAML", k + 1, ind + 1, "write the key as text on one line: quote it"));
            }
            let colon = match self.key_end(text) {
                Some(c) => c,
                None => return Err(self.err("expected a key and ':'", k + 1, ind + 1, "write key: value, or quote the text")),
            };
            let key = self.key_text(rstrip_set(&text[..colon], " \t"), k, ind)?;
            if out.contains_key(&key) {
                return Err(self.err(format!("the key {} appears twice", pyjson::repr_str(&key)), k + 1, ind + 1, "keep one"));
            }
            let mut kp = path.clone();
            kp.push(Step::Key(key.clone()));
            self.attach(&kp);
            let v = self.value(k, ind + colon + 1, ind, &kp)?;
            out.insert(key, v);
        }
        self.depth -= 1;
        Ok(Value::Dict(out))
    }

    fn value(&mut self, k: usize, col: usize, ind: usize, path: &CPath) -> R<Value> {
        let start = {
            let rest = &self.lines[k][col..];
            col + (rest.len() - lstrip_set(rest, " \t").len())
        };
        let len = self.lines[k].len();
        if start == len || self.lines[k][start] == '#' {
            if start < len {
                let said = s_of(rstrip_ws(&self.lines[k][start..]));
                self.comments.after.insert(path.clone(), said);
            }
            self.i = k + 1;
            self.skip();
            if self.i < self.n && !self.is_marker(self.i) {
                let li = self.indent(self.i)?;
                if li > ind {
                    return self.node(ind as i64, path);
                }
                if li == ind && is_dash(&self.lines[self.i][ind..]) {
                    return self.seq(ind, path);
                }
            }
            return Ok(Value::Null);
        }
        self.inline(k, start, ind as i64, path)
    }

    fn seq(&mut self, ind: usize, path: &CPath) -> R<Value> {
        self.enter()?;
        let mut out: Vec<Value> = Vec::new();
        loop {
            self.skip();
            if self.i >= self.n || self.is_marker(self.i) {
                break;
            }
            let k = self.i;
            let li = self.indent(k)?;
            if li < ind {
                break;
            }
            if li > ind {
                return Err(self.err(
                    "this line is indented more than the list items beside it",
                    k + 1,
                    li + 1,
                    "line it up with the other items, or put it under an item that ends with ':'",
                ));
            }
            if !is_dash(&self.lines[k][ind..]) {
                break; // a key beside a list written flush with it ends the list
            }
            let mut ip = path.clone();
            ip.push(Step::Index(out.len()));
            self.attach(&ip);
            self.lines[k][ind] = ' '; // the dash read as indentation: the rest is the item
            let line = self.lines[k].clone();
            let s = lstrip_set(&line[ind + 1..], " ").to_vec();
            if s.is_empty() || s[0] == '#' {
                if !s.is_empty() {
                    self.comments.after.insert(ip.clone(), s_of(rstrip_ws(&s)));
                }
                self.i = k + 1;
                let v = self.node(ind as i64, &ip)?;
                out.push(v);
                continue;
            }
            let c = line.len() - s.len();
            if is_dash(&s) {
                let v = self.seq(c, &ip)?;
                out.push(v);
            } else if self.key_end(&s).is_some() {
                let v = self.map(c, &ip)?;
                out.push(v);
            } else {
                let v = self.inline(k, c, ind as i64, &ip)?;
                out.push(v);
            }
        }
        self.depth -= 1;
        Ok(Value::List(out))
    }

    // -- scalars

    /// The rest of line k from column c, stripped: what an error quotes back.
    fn rest_stripped(&self, k: usize, c: usize) -> String {
        py_strip(&s_of(&self.lines[k][c..])).to_string()
    }

    fn inline(&mut self, k: usize, c: usize, parent: i64, path: &CPath) -> R<Value> {
        let ch = self.lines[k][c];
        if ch == '|' || ch == '>' {
            return self.block(k, c, parent, path).map(Value::Str);
        }
        if ch == '"' || ch == '\'' {
            return self.quoted(k, c, parent, path).map(Value::Str);
        }
        if ch == '[' || ch == '{' {
            return self.flow(k, c, parent, path);
        }
        if "&*!".contains(ch) {
            let what = match ch {
                '&' => "anchors",
                '*' => "aliases",
                _ => "tags",
            };
            let fix = if ch == '!' { "drop the tag: text, numbers, true/false and null need none" } else { "write the value out in full where it is used" };
            return Err(self.err(format!("{what} are not part of Rabbit Brain YAML"), k + 1, c + 1, fix));
        }
        let nxt = self.lines[k].get(c + 1).copied();
        if "-?:".contains(ch) && (nxt.is_none() || nxt == Some(' ') || nxt == Some('\t')) {
            let what = match ch {
                '-' => "a list cannot start on the line of its key",
                '?' => "complex keys (? ) are not part of Rabbit Brain YAML",
                _ => "a value cannot start with ': '",
            };
            let fix = if ch == '-' { "put a list on the lines below its key".to_string() } else { format!("quote it: \"{}\"", self.rest_stripped(k, c)) };
            return Err(self.err(what, k + 1, c + 1, fix));
        }
        if "@`%,]}".contains(ch) {
            return Err(self.err(
                format!("a value cannot start with {} unquoted", pyjson::repr_str(&ch.to_string())),
                k + 1,
                c + 1,
                format!("quote it: \"{}\"", self.rest_stripped(k, c)),
            ));
        }
        self.plain(k, c, parent, path)
    }

    fn check_plain(&self, text: &[char], k: usize, c: usize) -> R<()> {
        if contains_seq(text, ": ") || contains_seq(text, ":\t") || text.last() == Some(&':') {
            return Err(self.err("a plain value cannot contain ': ' or end with ':'", k + 1, c + 1, format!("quote it: \"{}\"", s_of(text))));
        }
        Ok(())
    }

    fn plain(&mut self, k: usize, c: usize, parent: i64, path: &CPath) -> R<Value> {
        let (text, mut comment) = cut_comment(&self.lines[k][c..]);
        let text = rstrip_set(&text, " \t").to_vec();
        self.check_plain(&text, k, c)?;
        let mut parts: Vec<String> = vec![s_of(&text)];
        self.i = k + 1;
        while comment.is_none() {
            let (mut j, mut blanks) = (self.i, 0usize);
            while j < self.n && self.blank(j) {
                j += 1;
                blanks += 1;
            }
            if j >= self.n || self.is_marker(j) {
                break;
            }
            let lj = self.indent(j)?;
            if (lj as i64) <= parent {
                break;
            }
            let t = self.lines[j][lj..].to_vec();
            if starts(&t, "#") {
                break;
            }
            if is_dash(&t) || self.key_end(&t).is_some() || starts(&t, "? ") || starts(&t, ": ") {
                return Err(self.err(
                    "this line is indented more than the keys beside it",
                    j + 1,
                    lj + 1,
                    "line it up with its sibling keys, or put it under a key that ends with ':'",
                ));
            }
            let (t2, cm) = cut_comment(&t);
            comment = cm;
            let t2 = rstrip_set(&t2, " \t").to_vec();
            self.check_plain(&t2, j, lj)?;
            parts.push(if blanks > 0 { "\n".repeat(blanks) } else { " ".to_string() });
            parts.push(s_of(&t2));
            self.i = j + 1;
        }
        if let Some(cm) = comment {
            self.comments.after.insert(path.clone(), cm);
        }
        if parts.len() > 1 {
            return Ok(Value::Str(parts.concat()));
        }
        resolve(&s_of(&text)).map_err(|a| self.err(a.message, k + 1, c + 1, a.fix))
    }

    /// A single- or double-quoted value, folded over lines the way YAML folds them.
    fn quoted(&mut self, k: usize, c: usize, parent: i64, path: &CPath) -> R<String> {
        let q = self.lines[k][c];
        let mut out: Vec<String> = Vec::new();
        let (mut row, mut j) = (k, c + 1);
        loop {
            let line = self.lines[row].clone();
            let mut keep = out.len();
            let mut escaped_break = false;
            while j < line.len() {
                let ch = line[j];
                if ch == q {
                    if q == '\'' && line.get(j + 1) == Some(&'\'') {
                        out.push("'".into());
                        keep = out.len();
                        j += 2;
                        continue;
                    }
                    self.i = row + 1;
                    self.after_value(row, j + 1, path)?;
                    return Ok(out.concat());
                }
                if ch == '\\' && q == '"' {
                    if j + 1 == line.len() {
                        escaped_break = true; // the next line starts afresh: j is set for it below
                        break;
                    }
                    let r = row;
                    let (nj, s) = escape(&line, j).map_err(|(m, cc)| self.err(m, r + 1, cc + 1, ""))?;
                    j = nj;
                    out.push(s);
                    keep = out.len();
                    continue;
                }
                out.push(ch.to_string());
                if ch != ' ' && ch != '\t' {
                    keep = out.len();
                }
                j += 1;
            }
            if !escaped_break {
                out.truncate(keep); // a line's trailing whitespace is not content
            }
            row += 1;
            let mut blanks = 0;
            while row < self.n && self.blank(row) {
                row += 1;
                blanks += 1;
            }
            if row >= self.n {
                let style = if q == '"' { "double" } else { "single" };
                return Err(self.err(format!("this {style}-quoted value never ends"), k + 1, c + 1, format!("close it with {q}")));
            }
            let li = self.indent(row)?;
            if (li as i64) <= parent || self.is_marker(row) {
                return Err(self.err(
                    "a quoted value's next line must be indented more than its key",
                    row + 1,
                    li + 1,
                    "indent it, or keep the value on one line",
                ));
            }
            if blanks > 0 {
                out.push("\n".repeat(blanks));
            } else if !escaped_break {
                out.push(" ".into());
            }
            let line = &self.lines[row];
            j = li;
            while j < line.len() && (line[j] == ' ' || line[j] == '\t') {
                j += 1;
            }
        }
    }

    fn after_value(&mut self, row: usize, col: usize, path: &CPath) -> R<()> {
        let rest = self.lines[row][col.min(self.lines[row].len())..].to_vec();
        let s = lstrip_set(&rest, " \t").to_vec();
        if s.is_empty() {
            return Ok(());
        }
        if s[0] == '#' && rest.len() > s.len() {
            self.comments.after.insert(path.clone(), s_of(rstrip_ws(&s)));
            return Ok(());
        }
        Err(self.err("unexpected text after the value", row + 1, col + rest.len() - s.len() + 1, "quote the whole value, or start a comment with ' #'"))
    }

    /// A literal (|) or folded (>) block, with its chomping and an optional indentation digit.
    fn block(&mut self, k: usize, c: usize, parent: i64, path: &CPath) -> R<String> {
        let line = self.lines[k].clone();
        let folded = line[c] == '>';
        let mut j = c + 1;
        let mut chomp: Option<char> = None;
        let mut explicit: Option<i64> = None;
        for _ in 0..2 {
            if j < line.len() && (line[j] == '+' || line[j] == '-') && chomp.is_none() {
                chomp = Some(line[j]);
                j += 1;
            } else if j < line.len() && ('1'..='9').contains(&line[j]) && explicit.is_none() {
                explicit = Some(line[j].to_digit(10).unwrap() as i64);
                j += 1;
            }
        }
        let rest = &line[j..];
        let s = lstrip_set(rest, " \t");
        if !s.is_empty() {
            if s[0] == '#' && rest.len() > s.len() {
                self.comments.after.insert(path.clone(), s_of(rstrip_ws(s)));
            } else {
                return Err(self.err(
                    format!("unexpected text after {}", s_of(&line[c..j])),
                    k + 1,
                    j + 1,
                    format!("the text of a {} block starts on the next line", line[c]),
                ));
            }
        }
        let mut width: Option<i64> = explicit.map(|e| parent + e);
        let mut body: Vec<Option<Chars>> = Vec::new();
        let mut lead: usize = 0;
        let mut row = k + 1;
        while row < self.n {
            let ln = self.lines[row].clone();
            if strip_set(&ln, " ").is_empty() {
                match width {
                    Some(w) if (ln.len() as i64) > w => body.push(Some(ln[w as usize..].to_vec())),
                    _ => {
                        body.push(None);
                        if width.is_none() {
                            lead = lead.max(ln.len());
                        }
                    }
                }
                row += 1;
                continue;
            }
            let li = ln.len() - lstrip_set(&ln, " ").len();
            if width.is_none() {
                if (li as i64) <= parent {
                    break;
                }
                if lead > li {
                    return Err(self.err("a blank line at the start of the block has more spaces than its first line of text", k + 2, 1, "empty the blank line"));
                }
                width = Some(li as i64);
            }
            let w = width.unwrap();
            if (li as i64) < w {
                break;
            }
            body.push(Some(ln[w as usize..].to_vec()));
            row += 1;
        }
        self.i = row;
        Ok(block_text(&body, folded, chomp))
    }

    /// A [ ] or { } collection, which may run over lines indented more than its key.
    fn flow(&mut self, k: usize, c: usize, parent: i64, path: &CPath) -> R<Value> {
        let mut pieces: Vec<Chars> = Vec::new();
        let mut depth: i64 = 0;
        let mut quote: Option<char> = None;
        let (mut row, mut j) = (k, c);
        let mut start = j;
        let end;
        loop {
            let line = self.lines[row].clone();
            let mut found_end: Option<usize> = None;
            while j < line.len() {
                let ch = line[j];
                if let Some(q) = quote {
                    if q == '"' && ch == '\\' {
                        j += 2;
                        continue;
                    }
                    if ch == q {
                        if q == '\'' && line.get(j + 1) == Some(&'\'') {
                            j += 2;
                            continue;
                        }
                        quote = None;
                    }
                    j += 1;
                    continue;
                }
                if (ch == '"' || ch == '\'') && value_starts(&pieces, &line, start, j) {
                    quote = Some(ch);
                } else if ch == '#' && j > start && (line[j - 1] == ' ' || line[j - 1] == '\t') {
                    self.comments.before.entry(path.clone()).or_default().push(s_of(rstrip_ws(&line[j..])));
                    break;
                } else if ch == '[' || ch == '{' {
                    depth += 1;
                } else if ch == ']' || ch == '}' {
                    depth -= 1;
                    if depth == 0 {
                        found_end = Some(j + 1);
                        break;
                    }
                }
                j += 1;
            }
            if let Some(e) = found_end {
                pieces.push(line[start..e].to_vec());
                end = e;
                break;
            }
            pieces.push(if j <= line.len() { line[start..j].to_vec() } else { line[start..].to_vec() });
            row += 1;
            while row < self.n && self.blank(row) {
                pieces.push(Vec::new()); // a blank line is a newline to a quoted value inside
                row += 1;
            }
            if row >= self.n {
                if let Some(q) = quote {
                    return Err(self.err("a quoted value inside this collection never ends", k + 1, c + 1, format!("close it with {q}")));
                }
                let open = self.lines[k][c];
                return Err(self.err(format!("this {open} never closes"), k + 1, c + 1, format!("close it with {}", if open == '[' { ']' } else { '}' })));
            }
            let li = self.indent(row)?;
            if (li as i64) <= parent {
                return Err(self.err("the lines of a [ ] or { } collection must be indented more than its key", row + 1, li + 1, "indent them"));
            }
            j = li;
            start = li;
        }
        let joined: Vec<char> = {
            let mut v = Vec::new();
            for (n, p) in pieces.iter().enumerate() {
                if n > 0 {
                    v.push('\n');
                }
                v.extend_from_slice(p);
            }
            v
        };
        let source = self.source.clone();
        let mut f = Flow { s: joined, p: 0, depth: 0, line: k + 1, col: c + 1, source };
        let value = f.parse()?;
        self.i = row + 1;
        self.after_value(row, end, path)?;
        Ok(value)
    }
}

fn value_starts(pieces: &[Chars], line: &[char], start: usize, j: usize) -> bool {
    let before_v = rstrip_set(&line[start..j], " \t").to_vec();
    let before = if before_v.is_empty() {
        let mut k = pieces.len() as i64 - 1;
        while k >= 0 && rstrip_set(&pieces[k as usize], " \t\n").is_empty() {
            k -= 1;
        }
        if k >= 0 {
            rstrip_set(&pieces[k as usize], " \t\n").to_vec()
        } else {
            Vec::new()
        }
    } else {
        before_v
    };
    before.is_empty() || "[{,:?".contains(*before.last().unwrap())
}

/// The index just after the quoted scalar that opens `text`, if it closes on this line.
fn quoted_end(text: &[char]) -> Option<usize> {
    let q = text[0];
    let mut j = 1;
    while j < text.len() {
        let ch = text[j];
        if q == '"' && ch == '\\' {
            j += 2;
            continue;
        }
        if ch == q {
            if q == '\'' && text.get(j + 1) == Some(&'\'') {
                j += 2;
                continue;
            }
            return Some(j + 1);
        }
        j += 1;
    }
    None
}

/// (value, index after it) of the quoted scalar opening `text`, which must close on the line. Err: (message, column).
fn quoted_line(text: &[char]) -> Result<(String, usize), (String, usize)> {
    let q = text[0];
    let mut out = String::new();
    let mut j = 1;
    while j < text.len() {
        let ch = text[j];
        if ch == q {
            if q == '\'' && text.get(j + 1) == Some(&'\'') {
                out.push('\'');
                j += 2;
                continue;
            }
            return Ok((out, j + 1));
        }
        if ch == '\\' && q == '"' {
            if j + 1 == text.len() {
                break;
            }
            let (nj, s) = escape(text, j)?;
            out.push_str(&s);
            j = nj;
            continue;
        }
        out.push(ch);
        j += 1;
    }
    Err(("a quoted key must close on its line".into(), 0))
}

fn escape(line: &[char], j: usize) -> Result<(usize, String), (String, usize)> {
    let e = match line.get(j + 1) {
        Some(e) => *e,
        None => return Err(("\\ is not an escape YAML knows".into(), j)),
    };
    let simple = match e {
        '0' => Some('\0'),
        'a' => Some('\u{7}'),
        'b' => Some('\u{8}'),
        't' | '\t' => Some('\t'),
        'n' => Some('\n'),
        'v' => Some('\u{b}'),
        'f' => Some('\u{c}'),
        'r' => Some('\r'),
        'e' => Some('\u{1b}'),
        ' ' => Some(' '),
        '"' => Some('"'),
        '/' => Some('/'),
        '\\' => Some('\\'),
        'N' => Some('\u{85}'),
        '_' => Some('\u{a0}'),
        'L' => Some('\u{2028}'),
        'P' => Some('\u{2029}'),
        _ => None,
    };
    if let Some(c) = simple {
        return Ok((j + 2, c.to_string()));
    }
    let n = match e {
        'x' => 2,
        'u' => 4,
        'U' => 8,
        _ => return Err((format!("\\{e} is not an escape YAML knows"), j)),
    };
    let h: String = line[(j + 2).min(line.len())..(j + 2 + n).min(line.len())].iter().collect();
    if h.chars().count() != n || !h.chars().all(|c| c.is_ascii_hexdigit()) {
        return Err((format!("\\{e} takes {n} hex digits"), j));
    }
    let o = u64::from_str_radix(&h, 16).unwrap();
    if (0xD800..=0xDFFF).contains(&o) {
        return Err((
            format!("\\{e}{h} is half of a surrogate pair, which YAML reads as a broken character: write the character itself, or \\U and its 8 hex digits"),
            j,
        ));
    }
    if o > 0x10FFFF {
        return Err((format!("\\{e}{h} is beyond Unicode"), j));
    }
    Ok((j + 2 + n, char::from_u32(o as u32).unwrap().to_string()))
}

/// The text of a block's lines, folded or literal, chomped: the same algorithm as PyYAML's scanner.
fn block_text(body: &[Option<Chars>], folded: bool, chomp: Option<char>) -> String {
    let n = body.len();
    let mut k = 0;
    while k < n && body[k].is_none() {
        k += 1;
    }
    let mut breaks: Vec<&str> = vec!["\n"; k];
    let mut chunks: Vec<String> = Vec::new();
    let mut line_break = "";
    while k < n {
        chunks.extend(breaks.iter().map(|s| s.to_string()));
        let text = body[k].as_ref().unwrap();
        let leading_non_space = !matches!(text.first(), Some(' ') | Some('\t'));
        chunks.push(s_of(text));
        line_break = "\n";
        k += 1;
        let mut count = 0;
        while k < n && body[k].is_none() {
            count += 1;
            k += 1;
        }
        breaks = vec!["\n"; count];
        if k < n {
            let nxt = body[k].as_ref().unwrap();
            if folded && leading_non_space && !matches!(nxt.first(), Some(' ') | Some('\t')) {
                if breaks.is_empty() {
                    chunks.push(" ".into());
                }
            } else {
                chunks.push(line_break.into());
            }
        }
    }
    if chomp != Some('-') {
        chunks.push(line_break.into());
    }
    if chomp == Some('+') {
        chunks.extend(breaks.iter().map(|s| s.to_string()));
    }
    chunks.concat()
}

/// A [ ] or { } collection, as one string with its lines joined by newlines and its comments cut.
struct Flow {
    s: Chars,
    p: usize,
    depth: usize,
    line: usize,
    col: usize,
    source: String,
}

impl Flow {
    fn err(&self, m: impl Into<String>, fix: impl Into<String>) -> YAMLError {
        YAMLError { message: m.into(), line: self.line, col: self.col, fix: fix.into(), source: self.source.clone() }
    }

    fn parse(&mut self) -> R<Value> {
        let v = self.value()?;
        self.ws();
        if self.p != self.s.len() {
            return Err(self.err("unexpected text after the collection", ""));
        }
        Ok(v)
    }

    fn ws(&mut self) {
        while self.p < self.s.len() && matches!(self.s[self.p], ' ' | '\t' | '\n') {
            self.p += 1;
        }
    }

    fn peek(&self) -> Option<char> {
        self.s.get(self.p).copied()
    }

    fn value(&mut self) -> R<Value> {
        self.ws();
        let ch = self.peek();
        match ch {
            Some('[') => return self.seq(),
            Some('{') => return self.map(),
            Some('"') | Some('\'') => return self.quoted().map(Value::Str),
            Some(c) if "&*!".contains(c) => {
                let what = match c {
                    '&' => "anchors",
                    '*' => "aliases",
                    _ => "tags",
                };
                return Err(self.err(format!("{what} are not part of Rabbit Brain YAML"), ""));
            }
            None => return Err(self.err("a value is missing inside the collection", "write null for an empty value")),
            Some(c) if "#@`%,]}|>".contains(c) => {
                let missing = ",]}".contains(c);
                return Err(self.err(
                    if missing { "a value is missing inside the collection".to_string() } else { format!("a value cannot start with {} unquoted", pyjson::repr_str(&c.to_string())) },
                    if missing { "write null for an empty value" } else { "quote it" },
                ));
            }
            _ => {}
        }
        self.plain()
    }

    fn nest(&mut self) -> R<()> {
        self.depth += 1;
        if self.depth > MAX_DEPTH {
            return Err(self.err(format!("nested more than {MAX_DEPTH} levels deep"), ""));
        }
        Ok(())
    }

    fn seq(&mut self) -> R<Value> {
        self.nest()?;
        self.p += 1;
        let mut out = Vec::new();
        loop {
            self.ws();
            if self.peek() == Some(']') {
                self.p += 1;
                self.depth -= 1;
                return Ok(Value::List(out));
            }
            out.push(self.value()?);
            self.ws();
            match self.peek() {
                Some(',') => self.p += 1,
                Some(']') => continue,
                Some(':') => return Err(self.err("a key: value pair inside [ ] is not part of Rabbit Brain YAML", "write a { } mapping inside the list")),
                _ => return Err(self.err("expected ',' or ']'", "")),
            }
        }
    }

    fn map(&mut self) -> R<Value> {
        self.nest()?;
        self.p += 1;
        let mut out = Dict::new();
        loop {
            self.ws();
            if self.peek() == Some('}') {
                self.p += 1;
                self.depth -= 1;
                return Ok(Value::Dict(out));
            }
            let key = match self.peek() {
                Some('"') | Some('\'') => Value::Str(self.quoted()?),
                Some('[') | Some('{') => return Err(self.err("a key must be text", "")),
                _ => self.plain()?,
            };
            let key = match key {
                Value::Str(s) => s,
                other => return Err(self.err(format!("a key is text, and {} is not", pyjson::py_repr(&other)), "quote it")),
            };
            self.ws();
            if self.peek() != Some(':') {
                return Err(self.err(format!("the key {} needs ': ' and a value", pyjson::repr_str(&key)), "write {key: value}"));
            }
            self.p += 1;
            if out.contains_key(&key) {
                return Err(self.err(format!("the key {} appears twice", pyjson::repr_str(&key)), "keep one"));
            }
            self.ws();
            let v = if matches!(self.peek(), Some(',') | Some('}')) { Value::Null } else { self.value()? };
            out.insert(key, v);
            self.ws();
            match self.peek() {
                Some(',') => self.p += 1,
                Some('}') => {}
                _ => return Err(self.err("expected ',' or '}'", "")),
            }
        }
    }

    fn quoted(&mut self) -> R<String> {
        let q = self.s[self.p];
        let s = &self.s;
        let mut out: Vec<String> = Vec::new();
        let mut j = self.p + 1;
        let mut keep = 0;
        while j < s.len() {
            let ch = s[j];
            if ch == q {
                if q == '\'' && s.get(j + 1) == Some(&'\'') {
                    out.push("'".into());
                    keep = out.len();
                    j += 2;
                    continue;
                }
                self.p = j + 1;
                return Ok(out.concat());
            }
            if ch == '\\' && q == '"' && s.get(j + 1) == Some(&'\n') {
                j += 2;
                while j < s.len() && (s[j] == ' ' || s[j] == '\t') {
                    j += 1;
                }
                keep = out.len();
                continue;
            }
            if ch == '\\' && q == '"' {
                let (nj, piece) = escape(s, j).map_err(|(m, _)| self.err(m, ""))?;
                j = nj;
                out.push(piece);
                keep = out.len();
                continue;
            }
            if ch == '\n' {
                out.truncate(keep);
                let mut blanks = 0;
                j += 1;
                loop {
                    while j < s.len() && (s[j] == ' ' || s[j] == '\t') {
                        j += 1;
                    }
                    if j < s.len() && s[j] == '\n' {
                        blanks += 1;
                        j += 1;
                        continue;
                    }
                    break;
                }
                out.push(if blanks > 0 { "\n".repeat(blanks) } else { " ".into() });
                keep = out.len();
                continue;
            }
            out.push(ch.to_string());
            if ch != ' ' && ch != '\t' {
                keep = out.len();
            }
            j += 1;
        }
        Err(self.err("a quoted value never ends", ""))
    }

    fn plain(&mut self) -> R<Value> {
        let start = self.p;
        let s = &self.s;
        if start >= s.len() {
            return Err(self.err("a value is missing inside the collection", "write null for an empty value"));
        }
        if s[start] == '?' && matches!(s.get(start + 1), None | Some(' ') | Some('\t') | Some('\n')) {
            return Err(self.err("complex keys (? ) are not part of Rabbit Brain YAML", "write the key as text on one line: quote it"));
        }
        while self.p < s.len() {
            let ch = s[self.p];
            if ",[]{}".contains(ch) {
                break;
            }
            if ch == ':' && (self.p + 1 == s.len() || " \t\n,[]{}".contains(s[self.p + 1])) {
                break;
            }
            self.p += 1;
        }
        let raw = strip_set(&s[start..self.p], " \t\n").to_vec();
        if raw.is_empty() {
            return Err(self.err("a value is missing inside the collection", "write null for an empty value"));
        }
        if "-?:".contains(raw[0]) && (raw.len() == 1 || " \t\n".contains(raw[1])) {
            return Err(self.err(format!("{} alone cannot start a value", pyjson::repr_str(&raw[0].to_string())), format!("quote it: \"{}\"", s_of(&raw))));
        }
        if raw.contains(&'\n') {
            let rs = s_of(&raw);
            let lines: Vec<String> = rs.split('\n').map(|x| x.trim_matches(|c| c == ' ' || c == '\t').to_string()).collect();
            if lines.iter().any(|x| x.is_empty()) {
                return Err(self.err("a blank line inside a value in [ ] or { }", "keep the value on one line"));
            }
            return Ok(Value::Str(lines.join(" ")));
        }
        resolve(&s_of(&raw)).map_err(|a| self.err(a.message, a.fix))
    }
}

/// Comments were collected by position; name list items by id or name where the list allows it.
fn identify(cm: &Comments, data: &Value) -> Comments {
    let conv = |path: &CPath| -> CPath {
        let mut out = Vec::new();
        let mut node: Option<&Value> = Some(data);
        for step in path {
            match (step, node) {
                (Step::Index(i), Some(Value::List(items))) if *i < items.len() => {
                    match item_scheme(items) {
                        Some(scheme) => out.push(Step::Named(scheme, items[*i].get_str(scheme).unwrap().to_string())),
                        None => out.push(step.clone()),
                    }
                    node = Some(&items[*i]);
                }
                (Step::Key(k), Some(Value::Dict(d))) if d.contains_key(k) => {
                    out.push(step.clone());
                    node = d.get(k);
                }
                _ => {
                    out.push(step.clone());
                    node = None;
                }
            }
        }
        out
    };
    let mut before: IndexMap<CPath, Vec<String>> = IndexMap::new();
    for (p, v) in &cm.before {
        before.entry(conv(p)).or_default().extend(v.iter().cloned());
    }
    let mut after: IndexMap<CPath, String> = IndexMap::new();
    for (p, v) in &cm.after {
        after.insert(conv(p), v.clone());
    }
    Comments { before, after, footer: cm.footer.clone() }
}

// ---------------------------------------------------------------- writing

#[derive(Debug)]
pub struct DumpError(pub String);

fn float_text(x: f64) -> Result<String, DumpError> {
    if !x.is_finite() {
        return Err(DumpError("Rabbit Brain YAML has no infinity or nan; record null for a value that is not a number".into()));
    }
    let r = pyfloat::repr(x);
    if r.contains('e') || r.contains('E') {
        let low = r.to_lowercase();
        let (m, e) = low.split_once('e').unwrap();
        let m = if m.contains('.') { m.to_string() } else { format!("{m}.0") };
        let e = if e.starts_with('+') || e.starts_with('-') { e.to_string() } else { format!("+{e}") };
        return Ok(format!("{m}e{e}"));
    }
    Ok(if r.contains('.') { r } else { format!("{r}.0") })
}

fn dq(s: &str) -> String {
    let mut out = String::from("\"");
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\t' => out.push_str("\\t"),
            '\r' => out.push_str("\\r"),
            c if is_bad(c) => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

/// Whether text can be written unquoted and read back as exactly itself, by every parser.
pub fn plain_ok(s: &str) -> bool {
    let first = match s.chars().next() {
        Some(c) => c,
        None => return false,
    };
    let last = s.chars().last().unwrap();
    if INDICATORS.contains(first) || first == ' ' || first == '\t' || last == ' ' || last == '\t' || last == ':' {
        return false;
    }
    if s.contains('\n') || s.contains('\r') || s.contains('\t') || s.contains(": ") || s.contains(" #") || s.chars().any(is_bad) {
        return false;
    }
    if s.starts_with("---") || s.starts_with("...") || re_date_prefix().is_match(s) {
        return false;
    }
    matches!(resolve(s), Ok(Value::Str(_)))
}

/// Whether multi-line text can be written as a | block and read back exactly.
fn block_ok(s: &str) -> bool {
    let body = s.trim_end_matches('\n');
    if !s.contains('\n') || body.trim_matches('\n').is_empty() {
        return false;
    }
    let lines: Vec<&str> = body.split('\n').collect();
    let first = match lines.iter().find(|x| !x.is_empty()) {
        Some(f) => f,
        None => return false,
    };
    if first.starts_with(' ') || first.starts_with('\t') {
        return false; // it would need an indentation digit; quoting is plainer
    }
    for x in &lines {
        if *x != x.trim_end_matches(|c| c == ' ' || c == '\t') || x.contains('\t') || x.contains('\r') || x.chars().any(is_bad) {
            return false;
        }
    }
    true
}

fn scalar(v: &Value) -> Result<String, DumpError> {
    Ok(match v {
        Value::Null => "null".into(),
        Value::Bool(true) => "true".into(),
        Value::Bool(false) => "false".into(),
        Value::Int(i) => i.to_string(),
        Value::BigInt(s) => s.clone(),
        Value::Float(f) => float_text(*f)?,
        Value::Str(s) => {
            if plain_ok(s) {
                s.clone()
            } else {
                dq(s)
            }
        }
        other => return Err(DumpError(format!("not a scalar: {}", other.type_name()))),
    })
}

fn key_text(k: &str) -> String {
    if plain_ok(k) {
        k.to_string()
    } else {
        dq(k)
    }
}

struct Emitter<'a> {
    cm: &'a Comments,
    out: Vec<String>,
    used: IndexSet<CPath>,
}

impl<'a> Emitter<'a> {
    fn before(&mut self, path: &CPath, ind: usize) {
        if let Some(cs) = self.cm.before.get(path) {
            if !cs.is_empty() {
                self.used.insert(path.clone());
                for c in cs {
                    if !c.is_empty() {
                        self.out.push(format!("{}{}", " ".repeat(ind), c));
                    } else if self.out.last().map_or(false, |x| !x.is_empty()) {
                        self.out.push(String::new());
                    }
                }
            }
        }
    }

    fn after(&mut self, path: &CPath) -> String {
        match self.cm.after.get(path) {
            Some(c) if !c.is_empty() => {
                self.used.insert(path.clone());
                format!(" {c}")
            }
            _ => String::new(),
        }
    }

    fn value_lines(&mut self, head: &str, v: &Value, ind: i64, path: &CPath, after: &str) -> Result<(), DumpError> {
        match v {
            Value::Dict(d) => {
                if d.is_empty() {
                    self.out.push(format!("{head} {{}}{after}"));
                } else {
                    self.out.push(format!("{head}{after}"));
                    self.map(d, (ind + 2) as usize, path)?;
                }
            }
            Value::List(items) => {
                if items.is_empty() {
                    self.out.push(format!("{head} []{after}"));
                } else {
                    self.out.push(format!("{head}{after}"));
                    self.seq(items, (ind + 2) as usize, path)?;
                }
            }
            Value::Str(s) if s.contains('\n') && block_ok(s) => {
                let trailing = s.len() - s.trim_end_matches('\n').len();
                let chomp = if trailing == 0 {
                    "-"
                } else if trailing == 1 {
                    ""
                } else {
                    "+"
                };
                self.out.push(format!("{head} |{chomp}{after}"));
                let pad = " ".repeat((ind + 2).max(0) as usize);
                let mut lines: Vec<&str> = s.trim_end_matches('\n').split('\n').collect();
                for _ in 0..trailing.saturating_sub(1) {
                    lines.push("");
                }
                for x in lines {
                    self.out.push(if x.is_empty() { String::new() } else { format!("{pad}{x}") });
                }
            }
            other => {
                let sc = scalar(other)?;
                self.out.push(format!("{head} {sc}{after}"));
            }
        }
        Ok(())
    }

    fn map(&mut self, d: &Dict, ind: usize, path: &CPath) -> Result<(), DumpError> {
        let sp = " ".repeat(ind);
        for (k, v) in d {
            let mut p = path.clone();
            p.push(Step::Key(k.clone()));
            self.before(&p, ind);
            let after = self.after(&p);
            self.value_lines(&format!("{sp}{}:", key_text(k)), v, ind as i64, &p, &after)?;
        }
        Ok(())
    }

    fn seq(&mut self, items: &[Value], ind: usize, path: &CPath) -> Result<(), DumpError> {
        let sp = " ".repeat(ind);
        let scheme = item_scheme(items);
        for (idx, v) in items.iter().enumerate() {
            let mut p = path.clone();
            p.push(match scheme {
                Some(s) => Step::Named(s, v.get_str(s).unwrap().to_string()),
                None => Step::Index(idx),
            });
            self.before(&p, ind);
            let after = self.after(&p);
            let has_before = |q: CPath| self.cm.before.get(&q).map_or(false, |x| !x.is_empty());
            let inline = after.is_empty()
                && match v {
                    Value::Dict(d) if !d.is_empty() => {
                        let mut q = p.clone();
                        q.push(Step::Key(d.keys().next().unwrap().clone()));
                        !has_before(q)
                    }
                    Value::List(l) if !l.is_empty() => {
                        let mut q = p.clone();
                        q.push(first_path(l));
                        !has_before(q)
                    }
                    _ => false,
                };
            if inline {
                let mark = self.out.len();
                match v {
                    Value::Dict(d) => self.map(d, ind + 2, &p)?,
                    Value::List(l) => self.seq(l, ind + 2, &p)?,
                    _ => {}
                }
                let line: String = self.out[mark].chars().skip(ind + 2).collect();
                self.out[mark] = format!("{sp}- {line}");
            } else {
                self.value_lines(&format!("{sp}-"), v, ind as i64, &p, &after)?;
            }
        }
        Ok(())
    }
}

fn first_path(items: &[Value]) -> Step {
    match item_scheme(items) {
        Some(s) => Step::Named(s, items[0].get_str(s).unwrap().to_string()),
        None => Step::Index(0),
    }
}

// ---------------------------------------------------------------- the API

/// Parse Rabbit Brain YAML: the data, and the comments anchored to it.
pub fn load(text: &str, source: &str) -> Result<Document, YAMLError> {
    let mut p = Parser::new(text, source)?;
    let data = p.document()?;
    Ok(Document { data, comments: p.comments })
}

pub fn loads(text: &str, source: &str) -> Result<Value, YAMLError> {
    Ok(load(text, source)?.data)
}

/// The canonical Rabbit Brain YAML of `data`, with `comments` put back where their keys and items still are; a comment
/// whose key is gone is kept at the end rather than dropped.
pub fn dumps(data: &Value, comments: Option<&Comments>) -> Result<String, DumpError> {
    let empty = Comments::default();
    let cm = comments.unwrap_or(&empty);
    let mut e = Emitter { cm, out: Vec::new(), used: IndexSet::new() };
    match data {
        Value::Dict(d) if !d.is_empty() => e.map(d, 0, &Vec::new())?,
        Value::List(l) if !l.is_empty() => e.seq(l, 0, &Vec::new())?,
        Value::Dict(_) => e.out.push("{}".into()),
        Value::List(_) => e.out.push("[]".into()),
        Value::Str(s) if s.contains('\n') && block_ok(s) => {
            e.value_lines("", data, -2, &Vec::new(), "")?;
            e.out[0] = e.out[0].trim_start().to_string();
        }
        other => {
            let s = scalar(other)?;
            e.out.push(s);
        }
    }
    let mut tail: Vec<String> = Vec::new();
    for (p, cs) in &cm.before {
        if !e.used.contains(p) {
            tail.extend(cs.iter().filter(|c| !c.is_empty()).cloned());
        }
    }
    for (p, c) in &cm.after {
        if !e.used.contains(p) {
            tail.push(c.clone());
        }
    }
    tail.extend(cm.footer.iter().cloned());
    e.out.extend(tail);
    while e.out.first().map_or(false, |x| x.is_empty()) {
        e.out.remove(0);
    }
    Ok(e.out.join("\n") + "\n")
}

/// The canonical form of a Rabbit Brain YAML text, its comments kept.
pub fn fmt(text: &str, source: &str) -> Result<String, String> {
    let d = load(text, source).map_err(|e| e.to_string())?;
    dumps(&d.data, Some(&d.comments)).map_err(|e| e.0)
}

/// The machine form of the data: keys sorted, no whitespace, no NaN. What every content hash is taken of.
pub fn canonical_json(data: &Value) -> Result<String, DumpError> {
    let o = Dumps { allow_nan: false, ..Dumps::canonical() };
    pyjson::dumps_with(data, &o).map_err(|_| DumpError("Rabbit Brain YAML has no infinity or nan; record null for a value that is not a number".into()))
}

pub fn sha256_hex(data: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(data);
    hex(&h.finalize())
}

pub fn hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push(HEX[(b >> 4) as usize] as char);
        s.push(HEX[(b & 15) as usize] as char);
    }
    s
}

pub fn content_sha(data: &Value) -> Result<String, DumpError> {
    Ok(sha256_hex(canonical_json(data)?.as_bytes()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_comments() {
        let text = "# head\nrb: 1\nkind: run   # the kind\nmetrics:\n  val_loss: 1.5\nlist:\n  - id: a\n    x: 1\n  # before b\n  - id: b\n    x: 2\n";
        let d = load(text, "t.yaml").unwrap();
        let out = dumps(&d.data, Some(&d.comments)).unwrap();
        assert_eq!(out, text.replace("run   #", "run #"));
    }

    #[test]
    fn ambiguous() {
        let e = load("a: yes\n", "f.yaml").unwrap_err();
        assert_eq!(e.to_string(), "f.yaml:1:4: yes is true or false to some YAML parsers and text to others; write true or false, or quote it for text: \"yes\"");
    }
}
