//! Edits rb makes to a file that was there before it (an agent's AGENTS.md, a project's .mcp.json): each one a splice
//! that keeps every byte of the file as it was and adds rb's part at one place, in the file's own line endings and
//! indentation, and the cut that takes exactly that part out again. A .mcp.json is never reformatted: the edit must
//! read back as the same object with rb's one member added, or it is not made.

use crate::pyjson;
use crate::value::Value;

/// An insertion: the file as it would be, and where rb's bytes go in it (offset and the bytes themselves), with the
/// bytes rb's take the place of there (`replaced`: none for an addition).
#[derive(Debug, Clone, PartialEq)]
pub struct Insert {
    pub text: String,
    pub offset: usize,
    pub added: String,
    pub replaced: String,
}

/// The line ending a file uses: "\r\n" where its lines end so, else "\n".
pub fn newline_of(text: &str) -> &'static str {
    let crlf = text.matches("\r\n").count();
    let lf = text.matches('\n').count();
    if crlf > 0 && crlf * 2 >= lf {
        "\r\n"
    } else {
        "\n"
    }
}

/// rb's section as rb writes it into `existing`: `block` in the file's line endings, without the line ending after the
/// end marker.
pub fn section_body(existing: &str, block: &str) -> String {
    let nl = newline_of(existing);
    let block = with_newlines(block, nl);
    block.strip_suffix(nl).unwrap_or(&block).to_string()
}

/// `text` (written with "\n") in the line endings `nl`.
fn with_newlines(text: &str, nl: &str) -> String {
    if nl == "\n" {
        text.to_string()
    } else {
        text.replace('\n', nl)
    }
}

/// Where each line of `text` that is `marker` and nothing else starts (a line's own "\r" before its "\n" aside): a
/// marker quoted inside the user's own sentence is no line of rb's.
fn marker_lines(text: &str, marker: &str) -> Vec<usize> {
    let mut at = Vec::new();
    let mut start = 0;
    for line in text.split_inclusive('\n') {
        if line.trim_end_matches('\n').trim_end_matches('\r') == marker {
            at.push(start);
        }
        start += line.len();
    }
    at
}

/// rb's marked section (`block`, written with "\n" and ending with one) added to `existing`: in place of the section
/// between rb's marker lines where the file holds one (each marker a line of its own, once), else at its end after a
/// blank line. Every byte of the file outside the section stays as it was.
pub fn section_add(existing: &str, block: &str, begin: &str, end: &str) -> Insert {
    let nl = newline_of(existing);
    let block = with_newlines(block, nl);
    if let Some((start, stop)) = section_span(existing, begin, end) {
        let body = block.strip_suffix(nl).unwrap_or(&block).to_string();
        let text = format!("{}{body}{}", &existing[..start], &existing[stop..]);
        return Insert { text, offset: start, added: body, replaced: existing[start..stop].to_string() };
    }
    let sep = if existing.is_empty() {
        String::new()
    } else if existing.ends_with('\n') {
        nl.to_string()
    } else {
        format!("{nl}{nl}")
    };
    let added = format!("{sep}{block}");
    Insert { text: format!("{existing}{added}"), offset: existing.len(), added, replaced: String::new() }
}

/// The file with rb's section taken out, as `section_add` added it: the bytes it added where they still are, else
/// the section between rb's markers when it is still `block` byte for byte (with the blank line before it). None when
/// the section is not there as rb wrote it.
pub fn section_cut(current: &str, added: &str, block: &str) -> Option<String> {
    if !added.is_empty() && current.matches(added).count() == 1 {
        return Some(current.replacen(added, "", 1));
    }
    let nl = newline_of(current);
    let block = with_newlines(block, nl);
    let body = block.strip_suffix(nl).unwrap_or(&block);
    if current.matches(body).count() != 1 {
        return None;
    }
    let start = current.find(body)?;
    Some(cut_span(current, start, start + body.len()))
}

/// Where rb's section is, between its marker lines (`begin` .. `end`, both included), when the file holds each once,
/// each a line of its own, the end after the begin.
pub fn section_span(text: &str, begin: &str, end: &str) -> Option<(usize, usize)> {
    let (b, e) = (marker_lines(text, begin), marker_lines(text, end));
    match (b.as_slice(), e.as_slice()) {
        ([start], [stop]) if stop > start => Some((*start, stop + end.len())),
        _ => None,
    }
}

/// The file without `text[start..stop]`, the line ending after it, and the blank line rb put before it.
pub fn cut_span(text: &str, start: usize, stop: usize) -> String {
    let nl = newline_of(text);
    let mut stop = stop;
    if text[stop..].starts_with(nl) {
        stop += nl.len();
    }
    let mut from = start;
    let blank = format!("{nl}{nl}");
    if text[..start].ends_with(&blank) {
        from -= nl.len();
    }
    format!("{}{}", &text[..from], &text[stop..])
}

/// Where the lines of `part` sit in `text`: (first line, last line), counted from 1.
pub fn lines_of(text: &str, part: &str) -> Option<(usize, usize)> {
    let start = text.find(part)?;
    let first = text[..start].matches('\n').count() + 1;
    let last = first + part.trim_end_matches(['\r', '\n']).matches('\n').count();
    Some((first, last))
}

// ---------------------------------------------------------------- JSON, read with where each part is

#[derive(Debug)]
enum Node {
    Obj { open: usize, close: usize, members: Vec<Member> },
    Arr { open: usize, close: usize },
    Leaf { start: usize, end: usize },
}

#[derive(Debug)]
struct Member {
    key: String,
    key_start: usize,
    value: Node,
}

impl Node {
    fn start(&self) -> usize {
        match self {
            Node::Obj { open, .. } | Node::Arr { open, .. } => *open,
            Node::Leaf { start, .. } => *start,
        }
    }

    /// Just past its last byte.
    fn end(&self) -> usize {
        match self {
            Node::Obj { close, .. } | Node::Arr { close, .. } => close + 1,
            Node::Leaf { end, .. } => *end,
        }
    }
}

struct Reader<'a> {
    b: &'a [u8],
    i: usize,
}

impl Reader<'_> {
    fn ws(&mut self) {
        while self.i < self.b.len() && matches!(self.b[self.i], b' ' | b'\t' | b'\r' | b'\n') {
            self.i += 1;
        }
    }

    fn string_end(&self, mut i: usize) -> Option<usize> {
        i += 1;
        while i < self.b.len() {
            match self.b[i] {
                b'\\' => i += 2,
                b'"' => return Some(i + 1),
                _ => i += 1,
            }
        }
        None
    }

    fn value(&mut self, depth: usize) -> Option<Node> {
        if depth > 64 {
            return None;
        }
        self.ws();
        let start = self.i;
        match *self.b.get(self.i)? {
            b'{' => {
                self.i += 1;
                let mut members = Vec::new();
                self.ws();
                if self.b.get(self.i) == Some(&b'}') {
                    self.i += 1;
                    return Some(Node::Obj { open: start, close: self.i - 1, members });
                }
                loop {
                    self.ws();
                    if self.b.get(self.i) != Some(&b'"') {
                        return None;
                    }
                    let key_start = self.i;
                    let key_end = self.string_end(self.i)?;
                    let key = match pyjson::loads(std::str::from_utf8(&self.b[key_start..key_end]).ok()?) {
                        Ok(Value::Str(k)) => k,
                        _ => return None,
                    };
                    self.i = key_end;
                    self.ws();
                    if self.b.get(self.i) != Some(&b':') {
                        return None;
                    }
                    self.i += 1;
                    let value = self.value(depth + 1)?;
                    members.push(Member { key, key_start, value });
                    self.ws();
                    match self.b.get(self.i)? {
                        b',' => self.i += 1,
                        b'}' => {
                            self.i += 1;
                            return Some(Node::Obj { open: start, close: self.i - 1, members });
                        }
                        _ => return None,
                    }
                }
            }
            b'[' => {
                self.i += 1;
                self.ws();
                if self.b.get(self.i) == Some(&b']') {
                    self.i += 1;
                    return Some(Node::Arr { open: start, close: self.i - 1 });
                }
                loop {
                    self.value(depth + 1)?;
                    self.ws();
                    match self.b.get(self.i)? {
                        b',' => self.i += 1,
                        b']' => {
                            self.i += 1;
                            return Some(Node::Arr { open: start, close: self.i - 1 });
                        }
                        _ => return None,
                    }
                }
            }
            b'"' => {
                self.i = self.string_end(self.i)?;
                Some(Node::Leaf { start, end: self.i })
            }
            _ => {
                while self.i < self.b.len() && !matches!(self.b[self.i], b',' | b'}' | b']' | b' ' | b'\t' | b'\r' | b'\n') {
                    self.i += 1;
                }
                (self.i > start).then_some(Node::Leaf { start, end: self.i })
            }
        }
    }
}

/// The document's top object, with where each part is, when `text` is one JSON object and nothing else.
fn read(text: &str) -> Option<Node> {
    let mut r = Reader { b: text.as_bytes(), i: 0 };
    let node = r.value(0)?;
    r.ws();
    (r.i == text.len() && matches!(node, Node::Obj { .. })).then_some(node)
}

/// The spaces or tabs before `pos` on its line, when nothing else is: where a part that starts its own line is indented.
fn indent_at(text: &str, pos: usize) -> Option<&str> {
    let line_start = text[..pos].rfind('\n').map_or(0, |i| i + 1);
    let lead = &text[line_start..pos];
    lead.chars().all(|c| c == ' ' || c == '\t').then_some(lead)
}

/// A value as JSON in the file's own style: one line (`compact` without spaces), or one part a line, indented.
fn render(v: &Value, ind: &str, unit: &str, nl: &str, colon: &str, multi: bool) -> String {
    if !multi {
        let (item_sep, key_sep) = if colon == ":" { (",", ":") } else { (", ", ": ") };
        return pyjson::dumps_with(v, &pyjson::Dumps { item_sep, key_sep, ensure_ascii: false, ..Default::default() }).unwrap_or_default();
    }
    let inner = format!("{ind}{unit}");
    match v {
        Value::Dict(d) if !d.is_empty() => {
            let parts: Vec<String> = d.iter().map(|(k, x)| format!("{inner}{}{colon}{}", pyjson::dumps(&Value::Str(k.clone())), render(x, &inner, unit, nl, colon, true))).collect();
            format!("{{{nl}{}{nl}{ind}}}", parts.join(&format!(",{nl}")))
        }
        Value::List(l) if !l.is_empty() => {
            let parts: Vec<String> = l.iter().map(|x| format!("{inner}{}", render(x, &inner, unit, nl, colon, true))).collect();
            format!("[{nl}{}{nl}{ind}]", parts.join(&format!(",{nl}")))
        }
        _ => pyjson::dumps(v),
    }
}

/// `"key": value` added as the last member of the object `obj` in `text`, in the file's own style.
fn add_member(text: &str, obj: &Node, key: &str, value: &Value, unit: &str) -> Option<Insert> {
    let Node::Obj { open, close, members } = obj else { return None };
    let nl = newline_of(text);
    let colon = match members.last() {
        Some(m) => {
            let between = &text[m.key_start..m.value.start()];
            if between.ends_with(": ") || between.contains(": ") {
                ": "
            } else {
                ":"
            }
        }
        None => {
            if text.contains("\": ") || !text.contains("\":") {
                ": "
            } else {
                ":"
            }
        }
    };
    let name = pyjson::dumps(&Value::Str(key.to_string()));
    match members.last() {
        Some(last) => match indent_at(text, last.key_start) {
            Some(ind) => {
                let added = format!(",{nl}{ind}{name}{colon}{}", render(value, ind, unit, nl, colon, true));
                let at = last.value.end();
                Some(Insert { text: format!("{}{added}{}", &text[..at], &text[at..]), offset: at, added, replaced: String::new() })
            }
            None => {
                let comma = if members.len() > 1 {
                    let prev = &members[members.len() - 2];
                    let between = &text[prev.value.end()..last.key_start];
                    if between.contains(' ') { ", " } else { "," }
                } else if colon == ":" {
                    ","
                } else {
                    ", "
                };
                let added = format!("{comma}{name}{colon}{}", render(value, "", unit, nl, colon, false));
                let at = last.value.end();
                Some(Insert { text: format!("{}{added}{}", &text[..at], &text[at..]), offset: at, added, replaced: String::new() })
            }
        },
        None => {
            let inner = &text[open + 1..*close];
            let at = open + 1;
            if text.contains('\n') {
                // the object's own line sets the indent; its members go one step in
                let own = indent_at(text, line_start(text, *open)).map(str::to_string).unwrap_or_default();
                let own = if own.is_empty() { leading(text, *open) } else { own };
                let ind = format!("{own}{unit}");
                let tail = if inner.contains('\n') { String::new() } else { format!("{nl}{own}") };
                let added = format!("{nl}{ind}{name}{colon}{}{tail}", render(value, &ind, unit, nl, colon, true));
                Some(Insert { text: format!("{}{added}{}", &text[..at], &text[at..]), offset: at, added, replaced: String::new() })
            } else {
                let added = format!("{name}{colon}{}", render(value, "", unit, nl, colon, false));
                Some(Insert { text: format!("{}{added}{}", &text[..at], &text[at..]), offset: at, added, replaced: String::new() })
            }
        }
    }
}

fn line_start(text: &str, pos: usize) -> usize {
    text[..pos].rfind('\n').map_or(0, |i| i + 1)
}

/// The spaces or tabs a line starts with, the line that holds `pos`.
fn leading(text: &str, pos: usize) -> String {
    let start = line_start(text, pos);
    text[start..].chars().take_while(|c| *c == ' ' || *c == '\t').collect()
}

/// The step a file indents by: its top object's first member's indent, past the object's own; two spaces otherwise.
fn unit_of(text: &str, top: &Node) -> String {
    if let Node::Obj { open, members, .. } = top {
        if let Some(first) = members.first() {
            if let Some(ind) = indent_at(text, first.key_start) {
                let own = leading(text, *open);
                if let Some(step) = ind.strip_prefix(own.as_str()).filter(|s| !s.is_empty()) {
                    return step.to_string();
                }
            }
        }
    }
    "  ".to_string()
}

/// Why rb does not add its member: the file is not one JSON object rb reads part by part, it holds a key twice, or
/// `mcpServers` is not an object.
pub const SHAPE: &str = "has a shape rb does not know";

/// rb's MCP server added to a .mcp.json that is there: `name` as the last member of its `mcpServers` (the object made
/// as the file's last member where it has none), in the file's own indentation, line endings and spacing. Err says why
/// not: it reads back as anything but the same object plus that member, or `name` is there already ("already").
pub fn mcp_add(text: &str, name: &str, member: &Value) -> Result<Insert, String> {
    let old = pyjson::loads(text).map_err(|_| "is not JSON rb can read".to_string())?;
    let Value::Dict(old_d) = &old else { return Err(SHAPE.into()) };
    let top = read(text).ok_or_else(|| SHAPE.to_string())?;
    let Node::Obj { members, .. } = &top else { return Err(SHAPE.into()) };
    if has_twice(members) {
        return Err(SHAPE.into());
    }
    let unit = unit_of(text, &top);
    let servers = members.iter().find(|m| m.key == "mcpServers");
    let mut want = old_d.clone();
    let insert = match servers {
        Some(m) => {
            let Node::Obj { members: inner, .. } = &m.value else { return Err(SHAPE.into()) };
            if has_twice(inner) {
                return Err(SHAPE.into());
            }
            if inner.iter().any(|x| x.key == name) {
                return Err("already".into());
            }
            if let Some(Value::Dict(s)) = want.get_mut("mcpServers") {
                s.insert(name.to_string(), member.clone());
            }
            add_member(text, &m.value, name, member, &unit)
        }
        None => {
            let wrapped = crate::obj! {name => member.clone()};
            want.insert("mcpServers".into(), wrapped.clone());
            add_member(text, &top, "mcpServers", &wrapped, &unit)
        }
    }
    .ok_or_else(|| SHAPE.to_string())?;
    match pyjson::loads(&insert.text) {
        Ok(v) if v == Value::Dict(want) => Ok(insert),
        _ => Err("cannot take rb's server without being written another way".into()),
    }
}

fn has_twice(members: &[Member]) -> bool {
    let mut seen = std::collections::HashSet::new();
    members.iter().any(|m| !seen.insert(m.key.as_str()))
}

/// The .mcp.json with the member `name` of its `mcpServers` taken out, when its value is `member`: the bytes of that
/// member and the comma beside it, nothing else. It must read back as the same object without that member.
pub fn mcp_cut(text: &str, name: &str, member: &Value) -> Option<String> {
    let old = pyjson::loads(text).ok()?;
    let top = read(text)?;
    let Node::Obj { members, .. } = &top else { return None };
    let servers = members.iter().find(|m| m.key == "mcpServers")?;
    let Node::Obj { open, close, members: inner } = &servers.value else { return None };
    let k = inner.iter().position(|m| m.key == name)?;
    if inner.iter().filter(|m| m.key == name).count() != 1 {
        return None;
    }
    if old.get("mcpServers").and_then(|s| s.get(name)) != Some(member) {
        return None;
    }
    let (from, to) = if inner.len() == 1 {
        (open + 1, *close)
    } else if k + 1 < inner.len() {
        (inner[k].key_start, inner[k + 1].key_start)
    } else {
        (inner[k - 1].value.end(), inner[k].value.end())
    };
    let cut = format!("{}{}", &text[..from], &text[to..]);
    let mut want = old.clone();
    if let Some(Value::Dict(s)) = want.as_dict_mut().and_then(|d| d.get_mut("mcpServers")) {
        s.shift_remove(name);
    }
    (pyjson::loads(&cut).ok()? == want).then_some(cut)
}

#[cfg(test)]
mod tests {
    use super::*;

    const BEGIN: &str = "<!-- b -->";
    const END: &str = "<!-- e -->";

    fn member() -> Value {
        crate::obj! {"command" => "rb", "args" => crate::list!["mcp"]}
    }

    /// The section goes at the end after a blank line, in the file's line endings, and comes out byte for byte.
    #[test]
    fn a_section_is_added_at_the_end_and_taken_out_exactly() {
        let block = "<!-- b -->\n\nrb's words\n\n<!-- e -->\n";
        for existing in ["# House rules\n", "# House rules", "# House rules\n\n\n", "# House rules\r\n\r\nKeep it tidy.\r\n", ""] {
            let ins = section_add(existing, block, BEGIN, END);
            assert!(ins.text.starts_with(existing), "{existing:?}");
            assert_eq!(&ins.text[ins.offset..], ins.added);
            if existing.contains("\r\n") {
                assert!(ins.added.contains("rb's words\r\n") && !ins.added.replace("\r\n", "").contains('\n'));
            }
            assert_eq!(section_cut(&ins.text, &ins.added, block).as_deref(), Some(existing), "{existing:?}");
        }
        // the user wrote after it: the section and its blank line go, their words stay
        let ins = section_add("# House rules\n", block, BEGIN, END);
        let edited = format!("{}\nTheir later line.\n", ins.text);
        assert_eq!(section_cut(&edited, &ins.added, block).as_deref(), Some("# House rules\n\nTheir later line.\n"));
        // an edit inside the section: it is not rb's as rb wrote it
        assert_eq!(section_cut(&ins.text.replace("rb's words", "their words"), &ins.added, block), None);
    }

    /// A section rb wrote before is replaced in place, every other byte kept, and the bytes it replaced are kept.
    #[test]
    fn a_section_already_there_is_replaced_in_place() {
        let had = "# A\n\n<!-- b -->\nold\n<!-- e -->\n\n# B\n";
        let ins = section_add(had, "<!-- b -->\nnew\n<!-- e -->\n", BEGIN, END);
        assert_eq!(ins.text, "# A\n\n<!-- b -->\nnew\n<!-- e -->\n\n# B\n");
        assert_eq!(ins.replaced, "<!-- b -->\nold\n<!-- e -->");
        let crlf = "# A\r\n<!-- b -->\r\nold\r\n<!-- e -->\r\n";
        assert_eq!(section_span(crlf, BEGIN, END), Some((5, crlf.len() - 2)));
    }

    /// Markers quoted inside a sentence are the user's words: rb's section goes at the end, and the sentence stays.
    #[test]
    fn markers_quoted_in_a_line_are_not_rb_s() {
        let had = "rb puts its section between `<!-- b -->` and `<!-- e -->`; leave those lines alone.\n";
        let ins = section_add(had, "<!-- b -->\nrb\n<!-- e -->\n", BEGIN, END);
        assert!(ins.text.starts_with(had) && ins.replaced.is_empty(), "{:?}", ins.text);
        assert_eq!(section_span(&ins.text, BEGIN, END), Some((had.len() + 1, ins.text.len() - 1)));
        let twice = "<!-- b -->\n<!-- e -->\n<!-- b -->\n<!-- e -->\n";
        assert_eq!(section_span(twice, BEGIN, END), None);
    }

    /// The member goes in as the file's own members are written, and the file reads as before plus it.
    #[test]
    fn an_mcp_member_is_added_in_the_file_s_own_style() {
        let four = "{\n    \"mcpServers\": {\n        \"other\": {\n            \"command\": \"other-server\"\n        }\n    }\n}\n";
        let ins = mcp_add(four, "rabbit-brain", &member()).unwrap();
        assert_eq!(
            ins.text,
            "{\n    \"mcpServers\": {\n        \"other\": {\n            \"command\": \"other-server\"\n        },\n        \"rabbit-brain\": {\n            \"command\": \"rb\",\n            \"args\": [\n                \"mcp\"\n            ]\n        }\n    }\n}\n"
        );
        assert!(ins.text.starts_with(&four[..four.find("        }\n    }").unwrap() + 9]));
        let tabs = "{\r\n\t\"mcpServers\": {\r\n\t\t\"other\": {\"command\": \"x\"}\r\n\t}\r\n}";
        let ins = mcp_add(tabs, "rabbit-brain", &member()).unwrap();
        assert!(ins.text.contains("},\r\n\t\t\"rabbit-brain\": {\r\n\t\t\t\"command\": \"rb\",\r\n"), "{:?}", ins.text);
        let one_line = "{\"mcpServers\":{\"other\":{\"command\":\"x\"}}}";
        assert_eq!(mcp_add(one_line, "rabbit-brain", &member()).unwrap().text, "{\"mcpServers\":{\"other\":{\"command\":\"x\"},\"rabbit-brain\":{\"command\":\"rb\",\"args\":[\"mcp\"]}}}");
        let no_servers = "{\n  \"other\": 1\n}\n";
        assert_eq!(mcp_add(no_servers, "rabbit-brain", &member()).unwrap().text,
                   "{\n  \"other\": 1,\n  \"mcpServers\": {\n    \"rabbit-brain\": {\n      \"command\": \"rb\",\n      \"args\": [\n        \"mcp\"\n      ]\n    }\n  }\n}\n");
        let empty = "{\n  \"mcpServers\": {}\n}\n";
        let ins = mcp_add(empty, "rabbit-brain", &member()).unwrap();
        assert_eq!(pyjson::loads(&ins.text).unwrap(), pyjson::loads("{\"mcpServers\": {\"rabbit-brain\": {\"command\": \"rb\", \"args\": [\"mcp\"]}}}").unwrap());
        for text in [four, tabs, one_line, no_servers, empty] {
            let ins = mcp_add(text, "rabbit-brain", &member()).unwrap();
            assert_eq!(format!("{}{}", &ins.text[..ins.offset], &ins.text[ins.offset + ins.added.len()..]), text);
        }
        assert_eq!(mcp_add(&mcp_add(four, "rabbit-brain", &member()).unwrap().text, "rabbit-brain", &member()).unwrap_err(), "already");
        assert!(mcp_add("{\"mcpServers\": []}", "rabbit-brain", &member()).is_err());
        assert!(mcp_add("{\"a\": 1, \"a\": 2}", "rabbit-brain", &member()).is_err());
        assert!(mcp_add("// a comment\n{}", "rabbit-brain", &member()).is_err());
    }

    /// The member comes out by its own bytes and the comma beside it, wherever it is, and only when it is rb's.
    #[test]
    fn an_mcp_member_is_taken_out_by_its_own_bytes() {
        let four = "{\n    \"mcpServers\": {\n        \"other\": {\n            \"command\": \"other-server\"\n        }\n    }\n}\n";
        let added = mcp_add(four, "rabbit-brain", &member()).unwrap().text;
        assert_eq!(mcp_cut(&added, "rabbit-brain", &member()).as_deref(), Some(four));
        let first = "{\"mcpServers\": {\"rabbit-brain\": {\"command\": \"rb\", \"args\": [\"mcp\"]}, \"other\": {}}}";
        assert_eq!(mcp_cut(first, "rabbit-brain", &member()).as_deref(), Some("{\"mcpServers\": {\"other\": {}}}"));
        let alone = "{\"mcpServers\": {\"rabbit-brain\": {\"command\": \"rb\", \"args\": [\"mcp\"]}}}";
        assert_eq!(mcp_cut(alone, "rabbit-brain", &member()).as_deref(), Some("{\"mcpServers\": {}}"));
        let theirs = "{\"mcpServers\": {\"rabbit-brain\": {\"type\": \"stdio\", \"command\": \"rb\", \"args\": [\"mcp\"]}}}";
        assert_eq!(mcp_cut(theirs, "rabbit-brain", &member()), None);
    }

    #[test]
    fn where_a_part_s_lines_are() {
        assert_eq!(lines_of("a\nb\nc\nd\n", "b\nc\n"), Some((2, 3)));
    }
}
