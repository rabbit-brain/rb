//! A SQLite database, read and never written. `rb run add --from <database> --table <table> --where <column>=<value>`
//! reads one row of it as a run, and `rb compile <database>` lists its tables. A database is told by SQLite's own first
//! bytes, never by its name.
//!
//! Read-only by construction: the connection is opened read-only (`mode=ro`), refuses writes (`PRAGMA query_only`)
//! and trusts nothing the database's schema asks it to run. A database in WAL mode is also opened `immutable`: a
//! read-only connection would otherwise make its -wal and -shm files beside it. Its file holds every committed write
//! while its -wal is empty or gone; when the -wal holds writes, reading them takes a connection that writes, and rb
//! says so instead of reading an older state. A rollback journal left by an unfinished write is refused the same way
//! (SQLite refuses it on a read-only connection, and rb on an immutable one). The database's size and time are taken
//! before and after a read: one that changed meanwhile is refused, never read half.

use crate::errors::{RBError, Res};
use crate::value::{Dict, Value};
use rusqlite::config::DbConfig;
use rusqlite::types::ValueRef;
use rusqlite::{ffi, Connection, OpenFlags};
use std::io::Read;
use std::path::{Path, PathBuf};

/// The first 16 bytes of every SQLite database.
const HEADER: &[u8; 16] = b"SQLite format 3\0";
/// The names a rowid answers to, in the order rb asks for it; a column with one of them takes that name for itself.
const ROWID: [&str; 3] = ["rowid", "_rowid_", "oid"];
/// How many rows a refusal shows of those it means.
const SHOWN: usize = 5;

/// A database's size and modification time: what tells rb it changed while rb read it.
pub type Stamp = (u64, i128);

pub fn stamp(path: &Path) -> Option<Stamp> {
    std::fs::metadata(path).ok().map(|m| (m.len(), crate::sys::mtime_ns(&m)))
}

/// Whether the file is a SQLite database: its first 16 bytes are SQLite's header, whatever its name. A file rb cannot
/// open or read is an error, never a file that is not one.
pub fn header(path: &Path) -> std::io::Result<bool> {
    let mut head = [0u8; 16];
    match std::fs::File::open(path)?.read_exact(&mut head) {
        Ok(()) => Ok(&head == HEADER),
        Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => Ok(false),
        Err(e) => Err(e),
    }
}

/// Whether the file is a SQLite database; one rb cannot read is not.
pub fn is_database(path: &Path) -> bool {
    header(path).unwrap_or(false)
}

/// One value of a row, as SQLite holds it.
#[derive(Debug, Clone, PartialEq)]
pub enum Cell {
    Null,
    Int(i64),
    Real(f64),
    Text(String),
    /// bytes: how many, and their sha256
    Blob(usize, String),
}

impl Cell {
    fn of(v: ValueRef) -> Cell {
        match v {
            ValueRef::Null => Cell::Null,
            ValueRef::Integer(i) => Cell::Int(i),
            ValueRef::Real(x) => Cell::Real(x),
            ValueRef::Text(t) => Cell::Text(String::from_utf8_lossy(t).into_owned()),
            ValueRef::Blob(b) => Cell::Blob(b.len(), crate::sources::sha256_bytes(b)),
        }
    }

    /// A number this cell holds; None for text, bytes and null.
    pub fn number(&self) -> Option<f64> {
        match self {
            Cell::Int(i) => Some(*i as f64),
            Cell::Real(x) if x.is_finite() => Some(*x),
            _ => None,
        }
    }

    /// The cell as a value of the record: a number or text, or None where it holds neither.
    pub fn value(&self) -> Option<Value> {
        match self {
            Cell::Int(i) => Some(Value::Int(*i)),
            Cell::Real(x) if x.is_finite() => Some(Value::Float(*x)),
            Cell::Text(s) => Some(Value::Str(s.clone())),
            _ => None,
        }
    }

    /// The cell as the row's identity keeps it: every value, bytes by their hash.
    fn identity(&self) -> Value {
        match self {
            Cell::Null => Value::Null,
            Cell::Blob(n, sha) => Value::Str(format!("{n} bytes, sha256 {sha}")),
            Cell::Real(x) if !x.is_finite() => Value::Str(self.said()),
            other => other.value().unwrap_or(Value::Null),
        }
    }

    /// The cell as a line says it.
    pub fn said(&self) -> String {
        match self {
            Cell::Null => "NULL".into(),
            Cell::Int(i) => i.to_string(),
            Cell::Real(x) => crate::pyjson::py_str(&Value::Float(*x)),
            Cell::Text(s) => value_said(&Value::Str(s.clone())),
            Cell::Blob(n, _) => format!("{n} {}", if *n == 1 { "byte" } else { "bytes" }),
        }
    }
}

/// A table: its name, its columns and how many rows it holds.
#[derive(Debug, Clone)]
pub struct Table {
    pub name: String,
    pub columns: Vec<String>,
    pub rows: i64,
}

/// The row a read found: its table, its rowid when the table has one, every column's value in the table's order, and
/// the columns it was picked by.
#[derive(Debug, Clone)]
pub struct Row {
    pub table: String,
    pub rowid: Option<i64>,
    pub cells: Vec<(String, Cell)>,
    pub key: Vec<String>,
}

impl Row {
    /// A column by its name: as written, else as SQLite matches names, ASCII letters in either case.
    pub fn column(&self, name: &str) -> Option<&(String, Cell)> {
        self.cells.iter().find(|(c, _)| c == name).or_else(|| self.cells.iter().find(|(c, _)| c.eq_ignore_ascii_case(name)))
    }

    /// The columns it was picked by, with the row's own values.
    pub fn key_values(&self) -> Dict {
        let mut d = Dict::new();
        for k in &self.key {
            let v = if ROWID.contains(&k.as_str()) && self.column(k).is_none() {
                self.rowid.map(Value::Int).unwrap_or(Value::Null)
            } else {
                self.column(k).map(|(_, c)| c.value().unwrap_or_else(|| c.identity())).unwrap_or(Value::Null)
            };
            d.insert(k.clone(), v);
        }
        d
    }

    /// What makes the row this row, for telling a second read of it from another row: its table, its rowid and every
    /// value it holds.
    pub fn identity(&self) -> Value {
        crate::obj! {
            "table" => self.table.clone(),
            "rowid" => self.rowid.map(Value::Int).unwrap_or(Value::Null),
            "values" => Value::Dict(self.cells.iter().map(|(c, v)| (c.clone(), v.identity())).collect())
        }
    }

    /// The row as a line names it: its table and rowid.
    pub fn place(&self) -> String {
        match self.rowid {
            Some(r) => format!("table {}, rowid {r}", self.table),
            None => format!("table {}", self.table),
        }
    }
}

/// `"name"`: a name SQLite reads as a name whatever it holds.
fn quoted(name: &str) -> String {
    format!("\"{}\"", name.replace('"', "\"\""))
}

/// The file beside the database whose name is the database's and `suffix`: its -wal, its -journal.
fn beside(path: &Path, suffix: &str) -> PathBuf {
    let mut name = path.as_os_str().to_os_string();
    name.push(suffix);
    PathBuf::from(name)
}

/// What a line calls the database, and the name of the files SQLite keeps beside it (-wal, -journal): beside the name
/// given, or for a link, beside the file it names.
struct Named<'a> {
    shown: &'a str,
    near: String,
}

impl<'a> Named<'a> {
    fn of(path: &Path, real: &Path, shown: &'a str) -> Named<'a> {
        let linked = std::fs::symlink_metadata(path).map_or(false, |m| m.file_type().is_symlink());
        let near = if !linked {
            shown.to_string()
        } else {
            let here = std::env::current_dir().and_then(crate::sys::canonicalize).unwrap_or_default();
            crate::sys::slashed(real.strip_prefix(&here).unwrap_or(real))
        };
        Named { shown, near }
    }
}

impl std::fmt::Display for Named<'_> {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        f.write_str(self.shown)
    }
}

/// The URI SQLite opens the database by: its absolute path, with what a URI reserves escaped, read-only, and immutable
/// for a WAL database.
fn uri(path: &Path, immutable: bool) -> String {
    let full = crate::sys::canonicalize(path).unwrap_or_else(|_| std::env::current_dir().unwrap_or_default().join(path));
    let full = crate::sys::slashed(&full);
    let mut out = String::from("file://");
    if !full.starts_with('/') {
        out.push('/');
    }
    for c in full.chars() {
        match c {
            '%' => out.push_str("%25"),
            '?' => out.push_str("%3f"),
            '#' => out.push_str("%23"),
            _ => out.push(c),
        }
    }
    out.push_str("?mode=ro");
    if immutable {
        out.push_str("&immutable=1");
    }
    out
}

fn cannot_write(shown: &Named, why: &str, fix: &str) -> RBError {
    RBError::msg_fix("E_OBJECT_INVALID", format!("{shown} cannot be read without writing to it: {why}. rb never writes to a database."), fix)
}

/// A write that did not finish, left in the database's rollback journal.
fn unfinished_write(shown: &Named) -> RBError {
    cannot_write(
        shown,
        &format!("a write that did not finish left {}-journal beside it, and only a connection that writes rolls it back", shown.near),
        "Open it once with the program that writes it, which rolls the unfinished write back; then read it again.",
    )
}

/// Whether a rollback journal holds a write that did not finish, as SQLite tells one: there, with a first byte that is
/// not zero (a finished write deletes it, cuts it to nothing or zeroes its start).
fn hot_journal(journal: &Path) -> bool {
    let mut first = [0u8; 1];
    std::fs::File::open(journal).and_then(|mut f| f.read_exact(&mut first)).is_ok() && first[0] != 0
}

/// What SQLite said, in rb's words: the database, what happened, and what to do.
fn said(e: &rusqlite::Error, shown: &Named) -> RBError {
    let (code, extended, message) = match e {
        rusqlite::Error::SqliteFailure(f, m) => (Some(f.code), f.extended_code, m.clone().unwrap_or_else(|| ffi::code_to_str(f.extended_code).to_string())),
        _ => (None, 0, String::new()),
    };
    match extended {
        ffi::SQLITE_READONLY_ROLLBACK => return unfinished_write(shown),
        ffi::SQLITE_READONLY_RECOVERY | ffi::SQLITE_READONLY_CANTINIT | ffi::SQLITE_READONLY_CANTLOCK | ffi::SQLITE_READONLY_DIRECTORY => {
            return cannot_write(shown, "SQLite has to recover or lock it first", "Read it when nothing has it open.")
        }
        _ => {}
    }
    match code {
        Some(ffi::ErrorCode::NotADatabase) => RBError::msg_fix(
            "E_OBJECT_INVALID",
            format!("{shown} starts as a SQLite database, and SQLite cannot read it: {message}."),
            "Check that the whole file is there; a copy taken while it was written can be cut short.",
        ),
        Some(ffi::ErrorCode::DatabaseCorrupt) => RBError::msg_fix(
            "E_OBJECT_INVALID",
            format!("{shown} is damaged, as SQLite reads it: {message}."),
            "Read a good copy of it.",
        ),
        Some(ffi::ErrorCode::DatabaseBusy | ffi::ErrorCode::DatabaseLocked) => RBError::msg_fix(
            "E_OBJECT_INVALID",
            format!("{shown} is locked by a program writing to it."),
            "Read it again when that write has finished.",
        ),
        Some(ffi::ErrorCode::CannotOpen | ffi::ErrorCode::PermissionDenied) => {
            RBError::msg_fix("E_FILE_NOT_FOUND", format!("{shown} could not be opened to read."), "Check that it is a file you can read.")
        }
        Some(_) => RBError::msg("E_OBJECT_INVALID", format!("{shown} could not be read: SQLite says {message}.")),
        None => RBError::msg("E_OBJECT_INVALID", format!("{shown} could not be read as a SQLite database.")),
    }
}

/// The database, open to read and nothing else: `path` is the file it is, a link followed.
fn open(path: &Path, shown: &Named) -> Res<Connection> {
    let mut head = Vec::new();
    match std::fs::File::open(path) {
        Ok(f) => {
            let _ = f.take(100).read_to_end(&mut head);
        }
        Err(e) => return Err(RBError::could_not_read(&e, shown.shown)),
    }
    if head.len() < 100 || &head[..16] != HEADER {
        return Err(RBError::msg_fix(
            "E_OBJECT_INVALID",
            format!("{shown} starts as a SQLite database and is cut short."),
            "Check that the whole file is there; a copy taken while it was written can be cut short.",
        ));
    }
    let wal = beside(path, "-wal");
    if std::fs::metadata(&wal).map_or(false, |m| m.len() > 0) {
        return Err(cannot_write(
            shown,
            &format!("its latest writes are still in {}-wal, and SQLite moves them into the database only for a connection that writes", shown.near),
            "Read it when nothing has it open: the program that writes to it moves those writes into the database when it closes it.",
        ));
    }
    // bytes 18 and 19 are 2 in WAL mode, the only one opened immutable: that skips SQLite's own check for an unfinished
    // write, so rb checks; a rollback database keeps SQLite's (which takes an empty -wal for none)
    let immutable = head[18] == 2 || head[19] == 2;
    if immutable && hot_journal(&beside(path, "-journal")) {
        return Err(unfinished_write(shown));
    }
    let flags = OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI | OpenFlags::SQLITE_OPEN_NO_MUTEX;
    let conn = Connection::open_with_flags(uri(path, immutable), flags).map_err(|e| said(&e, shown))?;
    for (setting, on) in [
        (DbConfig::SQLITE_DBCONFIG_TRUSTED_SCHEMA, false),
        (DbConfig::SQLITE_DBCONFIG_DEFENSIVE, true),
        (DbConfig::SQLITE_DBCONFIG_DQS_DML, false),
        (DbConfig::SQLITE_DBCONFIG_ENABLE_TRIGGER, false),
        (DbConfig::SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, true),
    ] {
        conn.set_db_config(setting, on).map_err(|e| said(&e, shown))?;
    }
    conn.execute_batch("PRAGMA query_only = ON; PRAGMA temp_store = MEMORY;").map_err(|e| said(&e, shown))?;
    Ok(conn)
}

/// `f` on the database open to read; refused when the database changed while it was read. A link is followed once,
/// so the -wal and -journal looked for are the ones SQLite keeps beside the file it names.
fn reading<T>(path: &Path, shown: &str, f: impl FnOnce(&Connection, &Named) -> Res<T>) -> Res<(T, Stamp)> {
    let real = crate::sys::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
    let named = Named::of(path, &real, shown);
    let Some(before) = stamp(&real) else {
        return Err(RBError::msg("E_FILE_NOT_FOUND", format!("No such file: {shown}")));
    };
    let conn = open(&real, &named)?;
    let got = f(&conn, &named);
    drop(conn);
    let got = got?;
    if stamp(&real) != Some(before) {
        return Err(RBError::msg_fix("E_OBJECT_INVALID", format!("{shown} changed while rb read it."), "Read it again."));
    }
    Ok((got, before))
}

/// The database's own tables, in the order its schema made them, each with whether it has a rowid; SQLite's tables,
/// views and virtual tables left out.
fn table_names(conn: &Connection, shown: &Named) -> Res<Vec<(String, bool)>> {
    let sql = "SELECT s.name, t.wr FROM sqlite_schema AS s JOIN pragma_table_list AS t ON t.name = s.name AND t.schema = 'main' \
               WHERE s.type = 'table' AND t.type = 'table' AND s.name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY s.rowid";
    let mut st = conn.prepare(sql).map_err(|e| said(&e, shown))?;
    let rows = st.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)? == 0))).map_err(|e| said(&e, shown))?;
    rows.collect::<Result<Vec<_>, _>>().map_err(|e| said(&e, shown))
}

fn columns_of(conn: &Connection, table: &str, shown: &Named) -> Res<Vec<String>> {
    let st = conn.prepare(&format!("SELECT * FROM {} LIMIT 0", quoted(table))).map_err(|e| said(&e, shown))?;
    Ok(st.column_names().into_iter().map(String::from).collect())
}

fn count(conn: &Connection, sql: &str, params: &[rusqlite::types::Value], shown: &Named) -> Res<i64> {
    conn.query_row(sql, rusqlite::params_from_iter(params.iter()), |r| r.get::<_, i64>(0)).map_err(|e| said(&e, shown))
}

/// The rows a query gives: with `rowid`, its first column is the rowid and the rest the table's columns.
fn fetch(conn: &Connection, sql: &str, params: &[rusqlite::types::Value], rowid: bool, shown: &Named) -> Res<Vec<(Option<i64>, Vec<Cell>)>> {
    let mut st = conn.prepare(sql).map_err(|e| said(&e, shown))?;
    let n = st.column_count();
    let mut rows = st.query(rusqlite::params_from_iter(params.iter())).map_err(|e| said(&e, shown))?;
    let mut out = Vec::new();
    while let Some(r) = rows.next().map_err(|e| said(&e, shown))? {
        let first = if rowid { 1 } else { 0 };
        let id = if rowid {
            match r.get_ref(0).map_err(|e| said(&e, shown))? {
                ValueRef::Integer(i) => Some(i),
                _ => None,
            }
        } else {
            None
        };
        let mut cells = Vec::with_capacity(n - first);
        for i in first..n {
            cells.push(Cell::of(r.get_ref(i).map_err(|e| said(&e, shown))?));
        }
        out.push((id, cells));
    }
    Ok(out)
}

/// A name among names: as written, else the one name that matches it with ASCII letters in either case.
fn pick<'a>(names: &'a [String], name: &str) -> Option<&'a String> {
    names.iter().find(|n| *n == name).or_else(|| {
        let same: Vec<&String> = names.iter().filter(|n| n.eq_ignore_ascii_case(name)).collect();
        (same.len() == 1).then(|| same[0])
    })
}

/// The tables as a refusal lists them.
fn listed(tables: &[String]) -> String {
    if tables.is_empty() {
        "none".into()
    } else {
        tables.join(", ")
    }
}

/// The database's tables with their columns and how many rows each holds.
pub fn tables(path: &Path, shown: &str) -> Res<Vec<Table>> {
    let (tables, _) = reading(path, shown, |conn, shown| {
        let mut out = Vec::new();
        for (name, _) in table_names(conn, shown)? {
            let columns = columns_of(conn, &name, shown)?;
            let rows = count(conn, &format!("SELECT count(*) FROM {}", quoted(&name)), &[], shown)?;
            out.push(Table { name, columns, rows });
        }
        Ok(out)
    })?;
    Ok(tables)
}

/// The database's tables, each with its columns, counting no rows; and the database's size and time as read.
pub fn table_columns(path: &Path, shown: &str) -> Res<(Vec<Table>, Stamp)> {
    reading(path, shown, |conn, shown| {
        let mut out = Vec::new();
        for (name, _) in table_names(conn, shown)? {
            let columns = columns_of(conn, &name, shown)?;
            out.push(Table { name, columns, rows: -1 });
        }
        Ok(out)
    })
}

/// A value --where gives, as SQLite compares it.
fn param(column: &str, v: &Value) -> Res<rusqlite::types::Value> {
    use rusqlite::types::Value as Sql;
    Ok(match v {
        Value::Int(i) => Sql::Integer(*i),
        Value::Bool(b) => Sql::Integer(i64::from(*b)),
        Value::Float(x) => Sql::Real(*x),
        Value::Str(s) | Value::BigInt(s) => Sql::Text(s.clone()),
        _ => return Err(RBError::msg("E_OBJECT_INVALID", format!("--where {column}: a column is compared with one number or one text."))),
    })
}

/// A value of a row as a line says it: text holding a comma, an '=' or a quote, empty or edged with spaces, in quotes,
/// so it reads as the one value it is; NULL as SQLite says it.
pub fn value_said(v: &Value) -> String {
    match v {
        Value::Null => "NULL".into(),
        Value::Str(s) if s.is_empty() || s.contains([',', '=', '"']) || s.trim() != s => {
            crate::pyjson::dumps_with(v, &crate::pyjson::Dumps { ensure_ascii: false, ..Default::default() }).unwrap_or_default()
        }
        Value::Str(s) => s.clone(),
        other => crate::pyjson::dumps(other),
    }
}

/// How column=value pairs read in a line: column=value, ...
pub fn pairs_said<'a>(pairs: impl IntoIterator<Item = (&'a String, &'a Value)>) -> String {
    pairs.into_iter().map(|(k, v)| format!("{k}={}", value_said(v))).collect::<Vec<_>>().join(", ")
}

/// The one row of `table` whose columns hold what `where_` gives (all of the table's rows when it gives nothing). No
/// row, or several, is refused: said with how many, and the first of them.
pub fn read_row(path: &Path, shown: &str, table: &str, where_: &[(String, Value)]) -> Res<(Row, Stamp)> {
    reading(path, shown, |conn, shown| {
        let tables = table_names(conn, shown)?;
        let names: Vec<String> = tables.iter().map(|(n, _)| n.clone()).collect();
        let Some(name) = pick(&names, table).cloned() else {
            return Err(RBError::msg_fix(
                "E_OBJECT_INVALID",
                format!("{shown} has no table {table}: its tables are {}.", listed(&names)),
                format!("Name one with --table; rb compile {} lists each with its columns and rows.", crate::cli::shlex_quote(shown.shown)),
            ));
        };
        let has_rowid = tables.iter().any(|(n, wr)| *n == name && *wr);
        let columns = columns_of(conn, &name, shown)?;
        // the rowid by a name no column takes
        let rowid = has_rowid.then(|| ROWID.iter().find(|r| !columns.iter().any(|c| c.eq_ignore_ascii_case(r))).copied()).flatten();
        let mut conds = Vec::new();
        let mut params = Vec::new();
        let mut key = Vec::new();
        for (col, v) in where_ {
            let name_of = match pick(&columns, col) {
                Some(c) => c.clone(),
                None if rowid.is_some() && ROWID.iter().any(|r| r.eq_ignore_ascii_case(col)) => rowid.unwrap().to_string(),
                None => {
                    return Err(RBError::msg_fix(
                        "E_OBJECT_INVALID",
                        format!("--where {col}: table {name} in {shown} has no column {col}; its columns are {}.", columns.join(", ")),
                        "Name the row by the columns it has.",
                    ))
                }
            };
            let target = if columns.contains(&name_of) { quoted(&name_of) } else { name_of.clone() };
            conds.push(format!("{target} = ?{}", params.len() + 1));
            params.push(param(col, v)?);
            key.push(name_of);
        }
        let filter = if conds.is_empty() { String::new() } else { format!(" WHERE {}", conds.join(" AND ")) };
        let head = rowid.map(|r| format!("{r}, ")).unwrap_or_default();
        let n = count(conn, &format!("SELECT count(*) FROM {}{filter}", quoted(&name)), &params, shown)?;
        let found = fetch(conn, &format!("SELECT {head}* FROM {}{filter} LIMIT {SHOWN}", quoted(&name)), &params, rowid.is_some(), shown)?;
        let row_of = |(id, cells): &(Option<i64>, Vec<Cell>)| Row {
            table: name.clone(),
            rowid: *id,
            cells: columns.iter().cloned().zip(cells.iter().cloned()).collect(),
            key: key.clone(),
        };
        let asked = pairs_said(where_.iter().map(|(k, v)| (k, v)));
        if n == 1 {
            return match found.first() {
                Some(only) => Ok(row_of(only)),
                None => Err(RBError::msg_fix("E_OBJECT_INVALID", format!("{shown} changed while rb read it."), "Read it again.")),
            };
        }
        if n == 0 {
            let first = fetch(conn, &format!("SELECT {head}* FROM {} LIMIT {SHOWN}", quoted(&name)), &[], rowid.is_some(), shown)?;
            let rows: Vec<Row> = first.iter().map(row_of).collect();
            let shown_rows: Vec<String> = rows
                .iter()
                .map(|r| {
                    let by = pairs_said(&r.key_values());
                    match (r.rowid, by.is_empty()) {
                        (Some(id), false) => format!("rowid {id} ({by})"),
                        (Some(id), true) => format!("rowid {id}"),
                        (None, _) => by,
                    }
                })
                .collect();
            let message = if rows.is_empty() {
                format!("Table {name} in {shown} has no rows.")
            } else {
                format!("No row of table {name} in {shown} has {asked}; its first rows: {}.", shown_rows.join("; "))
            };
            return Err(RBError::msg_fix("E_OBJECT_INVALID", message, "Give --where the values a row of it holds.").with_extra("matched", Value::Int(0)));
        }
        let rows: Vec<Row> = found.iter().map(row_of).collect();
        let ids: Vec<String> = rows.iter().filter_map(|r| r.rowid.map(|i| i.to_string())).collect();
        // the columns the rows shown differ in, beside the ones they were picked by: names and counts before measurements
        let mut differ: Vec<(bool, String)> = Vec::new();
        for (i, c) in columns.iter().enumerate() {
            let values: Vec<&Cell> = rows.iter().map(|r| &r.cells[i].1).collect();
            if !key.contains(c) && values.iter().any(|v| *v != values[0]) {
                let measured = values.iter().all(|v| matches!(v, Cell::Real(_) | Cell::Null));
                differ.push((measured, format!("{c} ({})", values.iter().map(|v| v.said()).collect::<Vec<_>>().join(", "))));
            }
        }
        differ.sort_by_key(|(measured, _)| *measured);
        let differ: Vec<String> = differ.into_iter().take(3).map(|(_, said)| said).collect();
        let mut message = if where_.is_empty() { format!("Table {name} in {shown} has {n} rows") } else { format!("{n} rows of table {name} in {shown} have {asked}") };
        if !ids.is_empty() {
            message += &format!(": rowid {}{}", ids.join(", "), if n as usize > ids.len() { ", ..." } else { "" });
        }
        message += &match (differ.is_empty(), n as usize > rows.len()) {
            (true, _) => "; they hold the same values.".to_string(),
            (false, false) => format!("; they differ in {}.", differ.join(", ")),
            (false, true) => format!("; the first {} differ in {}.", rows.len(), differ.join(", ")),
        };
        let fix = if differ.is_empty() && rowid.is_some() {
            "Name one by its rowid: --where rowid=<n>.".to_string()
        } else {
            "Add to --where the columns that tell the rows apart, until one row has them all.".to_string()
        };
        Err(RBError::msg_fix("E_OBJECT_INVALID", message, fix).with_extra("matched", Value::Int(n)))
    })
}

/// The tables as a refusal names them, with their row counts: evals (4 rows), configs (1 row).
pub fn tables_said(tables: &[Table]) -> String {
    listed(&tables.iter().map(|t| format!("{} ({} {})", t.name, t.rows, if t.rows == 1 { "row" } else { "rows" })).collect::<Vec<_>>())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A path SQLite opens as written: absolute, `/` between its parts, the characters a URI reserves escaped.
    #[test]
    fn a_path_becomes_the_uri_sqlite_opens_read_only() {
        let u = uri(Path::new("no such dir/a 50%?#.db"), false);
        assert!(u.starts_with("file:///") && u.ends_with("/no such dir/a 50%25%3f%23.db?mode=ro"), "{u}");
        assert!(uri(Path::new("a.db"), true).ends_with("/a.db?mode=ro&immutable=1"));
    }

    /// The connection rb reads with refuses every write, a change of schema included, and leaves the database and its
    /// directory as they were.
    #[test]
    fn the_connection_refuses_every_write_and_every_change_of_schema() {
        let dir = std::env::temp_dir().join(format!("rb-sqlite-read-only-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("results.db");
        Connection::open(&path).unwrap().execute_batch("CREATE TABLE evals (seed INTEGER, top1 REAL); INSERT INTO evals VALUES (1, 0.75);").unwrap();
        let before = std::fs::read(&path).unwrap();
        let conn = open(&path, &Named { shown: "results.db", near: "results.db".into() }).unwrap();
        for sql in ["INSERT INTO evals VALUES (2, 0.5)", "UPDATE evals SET top1 = 0", "CREATE TABLE notes (a)", "ALTER TABLE evals ADD COLUMN b",
                    "DROP TABLE evals", "CREATE INDEX by_seed ON evals (seed)", "PRAGMA user_version = 7", "VACUUM"] {
            assert!(conn.execute_batch(sql).is_err(), "{sql}");
        }
        assert_eq!(conn.query_row("SELECT count(*) FROM evals", [], |r| r.get::<_, i64>(0)).unwrap(), 1);
        drop(conn);
        assert_eq!(std::fs::read(&path).unwrap(), before);
        assert_eq!(std::fs::read_dir(&dir).unwrap().count(), 1);
        std::fs::remove_dir_all(&dir).unwrap();
    }

    /// A row is named by its table and rowid, and its key by the row's own values; a name is quoted whatever it holds.
    #[test]
    fn a_row_is_named_by_its_table_and_rowid() {
        let row = Row { table: "evals".into(), rowid: Some(3), cells: vec![("seed".into(), Cell::Int(1))], key: vec!["seed".into()] };
        assert_eq!(row.place(), "table evals, rowid 3");
        assert_eq!(row.key_values().get("seed"), Some(&Value::Int(1)));
        assert_eq!(Cell::Real(0.5).said(), "0.5");
        assert_eq!(Cell::Blob(1, String::new()).said(), "1 byte");
        assert_eq!(quoted("a\"b"), "\"a\"\"b\"");
    }
}
