//! A CSV or TSV table, read as rb reads a SQLite table: one row of it is a run (`rb run add --from <file>.csv --where
//! <column>=<value>`). The first line names the columns; a field in double quotes may hold the delimiter, a quote
//! written twice and a line break.

use crate::value::Value;

/// One row of a table: the line of the file it starts on, and its fields by column.
#[derive(Debug, Clone)]
pub struct CsvRow {
    pub line: usize,
    pub cells: Vec<(String, String)>,
}

impl CsvRow {
    /// The cell of a column, by its exact name.
    pub fn column(&self, name: &str) -> Option<&(String, String)> {
        self.cells.iter().find(|(c, _)| c == name)
    }
}

/// A table: its columns, and its rows in file order.
#[derive(Debug, Clone, Default)]
pub struct CsvTable {
    pub columns: Vec<String>,
    pub rows: Vec<CsvRow>,
}

/// Whether a path names a table rb reads by its rows: a .csv or a .tsv file.
pub fn is_table(path: &str) -> bool {
    let lower = path.to_lowercase();
    lower.ends_with(".csv") || lower.ends_with(".tsv")
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
        .map(|(line, fields)| CsvRow { line, cells: columns.iter().cloned().zip(fields.into_iter().map(|f| f.trim().to_string()).chain(std::iter::repeat(String::new()))).collect() })
        .collect();
    CsvTable { columns, rows }
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
    fn a_tsv_splits_at_tabs() {
        let t = parse("mode\ttop1\nint8\t75.6\n", "x.tsv");
        assert_eq!(t.rows[0].column("top1").unwrap().1, "75.6");
    }
}
