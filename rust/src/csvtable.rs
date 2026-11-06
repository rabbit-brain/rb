//! A CSV, TSV or JSON Lines table, read as rb reads a SQLite table: one row of it is a run (`rb run add --from
//! <file>.csv --where <column>=<value>`). A CSV's first line names the columns; a field in double quotes may hold the
//! delimiter, a quote written twice and a line break. A JSON Lines file holds one JSON object a line: each is a row,
//! its keys the columns, a nested object's keys joined with dots as a JSON file's are.

use crate::value::Value;

/// One row of a table: the line of the file it starts on, and its fields by column.
#[derive(Debug, Clone)]
pub struct CsvRow {
    pub line: usize,
    pub cells: Vec<(String, String)>,
    /// the columns whose cell is JSON text in a JSON Lines row: text, never read as a number
    pub texts: Vec<String>,
}

impl CsvRow {
    /// The cell of a column, by its exact name.
    pub fn column(&self, name: &str) -> Option<&(String, String)> {
        self.cells.iter().find(|(c, _)| c == name)
    }

    /// Whether a column's cell is text the row's own form says is text (a JSON string), whatever it spells.
    pub fn is_text(&self, name: &str) -> bool {
        self.texts.iter().any(|t| t == name)
    }

    /// A column's number, where its cell holds one.
    pub fn number_of(&self, name: &str) -> Option<f64> {
        if self.is_text(name) {
            return None;
        }
        self.column(name).and_then(|(_, v)| number(v))
    }

    /// A column's cell as a per-run value: its number where it holds one, else its text.
    pub fn value_of(&self, name: &str) -> Option<Value> {
        let (_, cell) = self.column(name)?;
        Some(if self.is_text(name) { Value::Str(cell.clone()) } else { value(cell) })
    }
}

/// A table: its columns, and its rows in file order.
#[derive(Debug, Clone, Default)]
pub struct CsvTable {
    pub columns: Vec<String>,
    pub rows: Vec<CsvRow>,
}

/// Whether a path names a table rb reads by its rows: a .csv, a .tsv or a JSON Lines file.
pub fn is_table(path: &str) -> bool {
    let lower = path.to_lowercase();
    lower.ends_with(".csv") || lower.ends_with(".tsv") || is_jsonl(path)
}

/// Whether a path names a JSON Lines file: .jsonl, or .ndjson.
pub fn is_jsonl(path: &str) -> bool {
    let lower = path.to_lowercase();
    lower.ends_with(".jsonl") || lower.ends_with(".ndjson")
}

/// What kind of table a path names, in rb's words: "CSV table", "TSV table" or "JSON Lines file".
pub fn kind_said(path: &str) -> &'static str {
    if is_jsonl(path) {
        "JSON Lines file"
    } else if path.to_lowercase().ends_with(".tsv") {
        "TSV table"
    } else {
        "CSV table"
    }
}

/// A CSV or TSV table whose first line reads as a data row (a number with a decimal point): the numbers that line
/// holds, as rb says them, and how many rows the table holds, that line among them. rb names no column from that line,
/// and so offers no column (nor an alias for one) that is a number.
fn no_header(t: &CsvTable) -> Option<(Vec<String>, usize)> {
    let numbers: Vec<&String> = t.columns.iter().filter(|c| number(c).is_some() && c.contains('.')).collect();
    if numbers.is_empty() {
        return None;
    }
    let rows = t.rows.iter().filter(|r| r.cells.iter().any(|(_, v)| !v.is_empty())).count() + 1;
    Some((numbers.iter().take(4).map(|c| c.to_string()).collect(), rows))
}

/// How many rows a CSV or TSV table holds, its first line among them, where that line reads as a data row: None where
/// it reads as a header (or the file is JSON Lines, whose rows name their keys).
pub fn rows_without_header(text: &str, path: &str) -> Option<usize> {
    if is_jsonl(path) {
        return None;
    }
    no_header(&parse(text, path)).map(|(_, rows)| rows)
}

/// The table a file holds, told by its name: a JSON Lines file's rows, or a CSV or TSV table's. A JSON Lines line that
/// is not a JSON object is said with its line.
pub fn read(text: &str, path: &str) -> Result<CsvTable, String> {
    if is_jsonl(path) {
        parse_jsonl(text, path)
    } else {
        let t = parse(text, path);
        if let Some((shown, rows)) = no_header(&t) {
            return Err(format!(
                "{path} seems to have no header line: its first line reads as a data row (it holds {}), so rb cannot name its columns; it holds {rows} row{}, that line among them",
                shown.join(", "),
                if rows == 1 { "" } else { "s" }
            ));
        }
        Ok(t)
    }
}

/// A JSON Lines file's rows: one JSON object a line (blank lines skipped), its keys the columns in the order they are
/// first met, a nested object's keys joined with dots. A row without a column holds nothing there.
pub fn parse_jsonl(text: &str, path: &str) -> Result<CsvTable, String> {
    let mut columns: Vec<String> = Vec::new();
    let mut parsed: Vec<(usize, crate::value::Dict)> = Vec::new();
    for (i, line) in text.trim_start_matches('\u{feff}').split('\n').enumerate() {
        let line = line.strip_suffix('\r').unwrap_or(line);
        if line.trim().is_empty() {
            continue;
        }
        let doc = crate::pyjson::loads(line).map_err(|e| format!("{path}, line {}, is not JSON: {}", i + 1, e.to_string().lines().next().unwrap_or("")))?;
        if !matches!(doc, Value::Dict(_)) {
            return Err(format!("{path}, line {}, is not a JSON object, so it is no row", i + 1));
        }
        // a key that two of the object's own keys both read as ("eval.top1" beside "eval": {"top1"}) names no one cell
        let mut flat = crate::value::Dict::new();
        if let Some(twice) = flatten_once(&doc, "", &mut flat) {
            return Err(format!("{path}, line {}, holds two keys that both read as {twice}, so rb cannot tell which is the cell", i + 1));
        }
        for k in flat.keys() {
            if !columns.contains(k) {
                columns.push(k.clone());
            }
        }
        parsed.push((i + 1, flat));
    }
    let rows = parsed
        .into_iter()
        .map(|(line, flat)| {
            let mut texts = Vec::new();
            let cells = columns
                .iter()
                .map(|c| {
                    let cell = match flat.get(c) {
                        None | Some(Value::Null) => String::new(),
                        Some(Value::Str(s)) => {
                            texts.push(c.clone());
                            s.clone()
                        }
                        Some(Value::Bool(b)) => if *b { "true" } else { "false" }.to_string(),
                        Some(Value::Int(n)) => n.to_string(),
                        Some(Value::Float(f)) => crate::pyfloat::repr(*f),
                        Some(v) => {
                            texts.push(c.clone());
                            crate::pyjson::dumps(v)
                        }
                    };
                    (c.clone(), cell)
                })
                .collect();
            CsvRow { line, cells, texts }
        })
        .collect();
    Ok(CsvTable { columns, rows })
}

/// The records of `text`, each with the line it starts on, split at `delim` outside double quotes.
fn records(text: &str, delim: char) -> Vec<(usize, Vec<String>)> {
    let mut out = Vec::new();
    let mut fields: Vec<String> = Vec::new();
    let mut field = String::new();
    let (mut quoted, mut line, mut start) = (false, 1usize, 1usize);
    let mut chars = text.trim_start_matches('\u{feff}').chars().peekable();
    let mut any = false;
    while let Some(c) = chars.next() {
        any = true;
        if quoted {
            match c {
                '"' if chars.peek() == Some(&'"') => {
                    chars.next();
                    field.push('"');
                }
                '"' => quoted = false,
                '\n' => {
                    line += 1;
                    field.push(c);
                }
                _ => field.push(c),
            }
            continue;
        }
        match c {
            '"' if field.is_empty() => quoted = true,
            c if c == delim => fields.push(std::mem::take(&mut field)),
            '\r' if chars.peek() == Some(&'\n') => {}
            '\n' => {
                fields.push(std::mem::take(&mut field));
                if !(fields.len() == 1 && fields[0].trim().is_empty()) {
                    out.push((start, std::mem::take(&mut fields)));
                }
                fields.clear();
                line += 1;
                start = line;
                any = false;
            }
            _ => field.push(c),
        }
    }
    if any {
        fields.push(field);
        if !(fields.len() == 1 && fields[0].trim().is_empty()) {
            out.push((start, fields));
        }
    }
    out
}

/// The table `text` holds, its delimiter told by its name (a tab for .tsv, a comma otherwise).
pub fn parse(text: &str, path: &str) -> CsvTable {
    let delim = if path.to_lowercase().ends_with(".tsv") { '\t' } else { ',' };
    let mut recs = records(text, delim).into_iter();
    let Some((_, header)) = recs.next() else { return CsvTable::default() };
    let columns: Vec<String> = header.iter().map(|h| h.trim().to_string()).collect();
    let rows = recs
        .map(|(line, fields)| CsvRow { line, cells: columns.iter().cloned().zip(fields.into_iter().map(|f| f.trim().to_string()).chain(std::iter::repeat(String::new()))).collect(), texts: Vec::new() })
        .collect();
    CsvTable { columns, rows }
}

/// `doc`'s keys joined with dots into `out`, as a JSON file's are: the first key two of them both read as, where any.
fn flatten_once(doc: &Value, prefix: &str, out: &mut crate::value::Dict) -> Option<String> {
    if let Value::Dict(d) = doc {
        for (k, v) in d {
            let key = if prefix.is_empty() { k.clone() } else { format!("{prefix}.{k}") };
            match v {
                Value::Dict(inner) if !inner.is_empty() => {
                    if let Some(t) = flatten_once(v, &key, out) {
                        return Some(t);
                    }
                }
                _ => {
                    if out.contains_key(&key) {
                        return Some(key);
                    }
                    out.insert(key, v.clone());
                }
            }
        }
    }
    None
}

/// A cell's number, where it holds one.
pub fn number(cell: &str) -> Option<f64> {
    let t = cell.trim();
    if t.is_empty() {
        return None;
    }
    match crate::sources::parse_number(t) {
        Some(Value::Int(i)) => Some(i as f64),
        Some(Value::Float(f)) if f.is_finite() => Some(f),
        _ => None,
    }
}

/// A cell as a per-run value: its number where it holds one, else its text.
pub fn value(cell: &str) -> Value {
    match crate::sources::parse_number(cell.trim()) {
        Some(v @ Value::Int(_)) => v,
        Some(Value::Float(f)) if f.is_finite() => Value::Float(f),
        _ => Value::Str(cell.trim().to_string()),
    }
}

/// Whether a cell holds what `--where` gives: the same text, or the same number.
pub fn matches(cell: &str, want: &Value) -> bool {
    let t = cell.trim();
    match want {
        Value::Str(s) => t == s || matches!((number(t), number(s)), (Some(a), Some(b)) if a == b),
        Value::Int(i) => number(t) == Some(*i as f64),
        Value::Float(f) => number(t) == Some(*f),
        Value::Bool(b) => t.eq_ignore_ascii_case(if *b { "true" } else { "false" }),
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_table_reads_by_its_header_with_quoted_fields_and_their_lines() {
        let t = parse("mode,bits,note,top1\nint8,8,\"one, two\",75.6\nfp32,32,\"a\nb\",76.1\n\nint4,4,,\n", "x.csv");
        assert_eq!(t.columns, ["mode", "bits", "note", "top1"]);
        assert_eq!(t.rows.len(), 3);
        assert_eq!(t.rows[0].line, 2);
        assert_eq!(t.rows[0].column("note").unwrap().1, "one, two");
        assert_eq!(t.rows[1].column("note").unwrap().1, "a\nb");
        assert_eq!(t.rows[2].line, 6);
        assert_eq!(number(&t.rows[0].column("top1").unwrap().1), Some(75.6));
        assert_eq!(number(&t.rows[2].column("top1").unwrap().1), None);
        assert!(matches("8", &Value::Int(8)) && matches("8.0", &Value::Str("8".into())) && !matches("80", &Value::Int(8)));
    }

    #[test]
    fn a_json_lines_file_is_a_table_of_its_objects() {
        let t = read("{\"mode\": \"int8\", \"seed\": 1, \"eval\": {\"top1\": 75.6}, \"tag\": \"3\"}\n\n{\"mode\": \"fp32\", \"seed\": 2, \"eval\": {\"top1\": 76.1}, \"ok\": true}\n", "r.jsonl").unwrap();
        assert_eq!(t.columns, ["mode", "seed", "eval.top1", "tag", "ok"]);
        assert_eq!(t.rows.len(), 2);
        assert_eq!(t.rows[1].line, 3);
        assert_eq!(t.rows[0].number_of("eval.top1"), Some(75.6));
        assert_eq!(t.rows[0].number_of("tag"), None);
        assert_eq!(t.rows[0].value_of("tag"), Some(Value::Str("3".into())));
        assert_eq!(t.rows[1].value_of("seed"), Some(Value::Int(2)));
        assert_eq!(t.rows[1].column("tag").unwrap().1, "");
        assert!(read("{\"a\": 1}\n[1]\n", "r.jsonl").unwrap_err().contains("line 2"));
    }

    #[test]
    fn a_tsv_splits_at_tabs() {
        let t = parse("mode\ttop1\nint8\t75.6\n", "x.tsv");
        assert_eq!(t.rows[0].column("top1").unwrap().1, "75.6");
    }
}
