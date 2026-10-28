//! Error codes: one table feeds `errors[]` in the JSON envelope, `rb docs`, and the tests.
//!
//! Exit codes: 0 ok · 1 a check failed · 2 rb could not do what was asked, and says why · 3 a fault in rb itself.
//!
//! Every failure is said one way: `rb <command>: <what happened>`, then `  → <what to do>` (a command line that does
//! not parse shows the usage first); with --json, the same in `errors[]`. When the system under rb is what failed (a
//! file it cannot write, an address it cannot reach), rb's sentence names the file or the address and carries the
//! system's own words for why (`sys::os_words`).

use crate::errors_table::ERRORS;
use crate::value::{Dict, Value};

pub const EXIT_OK: i32 = 0;
pub const EXIT_CHECK_FAILED: i32 = 1;
pub const EXIT_INVALID: i32 = 2;
pub const EXIT_ENVIRONMENT: i32 = 3;

#[derive(Debug, Clone)]
pub struct RBError {
    pub code: String,
    pub message: String,
    pub fix: String,
    pub exit_code: i32,
    pub problems: Vec<String>,
    pub extra: Dict,
}

pub fn meaning(code: &str) -> Option<(&'static str, &'static str)> {
    ERRORS.iter().find(|(c, _, _)| *c == code).map(|(_, m, f)| (*m, *f))
}

impl RBError {
    /// RBError(code): the table's meaning and fix.
    pub fn new(code: &str) -> RBError {
        RBError::build(code, None, None)
    }
    /// RBError(code, message=...)
    pub fn msg(code: &str, message: impl Into<String>) -> RBError {
        RBError::build(code, Some(message.into()), None)
    }
    /// RBError(code, message=..., fix=...)
    pub fn msg_fix(code: &str, message: impl Into<String>, fix: impl Into<String>) -> RBError {
        RBError::build(code, Some(message.into()), Some(fix.into()))
    }
    pub fn build(code: &str, message: Option<String>, fix: Option<String>) -> RBError {
        let (m, f) = meaning(code).unwrap_or(("Unexpected failure.", meaning("E_INTERNAL").map(|x| x.1).unwrap_or("")));
        let exit_code = EXIT_INVALID;
        RBError {
            code: code.to_string(),
            message: message.unwrap_or_else(|| m.to_string()),
            fix: fix.unwrap_or_else(|| f.to_string()),
            exit_code,
            problems: Vec::new(),
            extra: Dict::new(),
        }
    }
    /// A file or a directory rb could not write, make or replace: the path, the system's words for why, and the
    /// directory to look at.
    pub fn could_not_write(e: &std::io::Error, path: &std::path::Path) -> RBError {
        // a file where a directory has to be: the system's words for that ("File exists", "Not a directory") name
        // nothing, so rb names the file that is in the way
        if matches!(e.kind(), std::io::ErrorKind::AlreadyExists | std::io::ErrorKind::NotADirectory) {
            if let Some(in_the_way) = path.ancestors().skip(1).find(|a| a.is_file()) {
                return RBError::msg_fix(
                    "E_WRITE_FAILED",
                    format!("Could not write {}: {} is a file, not a directory", crate::sys::slashed(path), crate::sys::slashed(in_the_way)),
                    "Give a path whose directories exist or can be made.",
                );
            }
        }
        let dir = match path.parent().filter(|p| !p.as_os_str().is_empty()) {
            Some(p) => crate::sys::slashed(p),
            None => "the current directory".to_string(),
        };
        RBError::msg_fix(
            "E_WRITE_FAILED",
            format!("Could not write {}: {}", crate::sys::slashed(path), crate::sys::os_words(e)),
            format!("Check that {dir} is a directory you can write to."),
        )
    }
    /// A file rb was pointed at and could not read. `shown` is the path as it was given.
    pub fn could_not_read(e: &std::io::Error, shown: &str) -> RBError {
        if e.kind() == std::io::ErrorKind::NotFound {
            return RBError::msg("E_FILE_NOT_FOUND", format!("No such file: {shown}"));
        }
        RBError::msg_fix("E_FILE_NOT_FOUND", format!("{shown} could not be read: {}", crate::sys::os_words(e)), "Check that it is a file you can read.")
    }
    /// A fault in rb itself: something its own code did not expect. The one failure that is not the caller's to fix,
    /// so it exits 3 and asks to be reported.
    pub fn fault(what: impl Into<String>) -> RBError {
        RBError::msg("E_INTERNAL", format!("rb itself failed: {}", what.into())).with_exit(EXIT_ENVIRONMENT)
    }
    pub fn with_problems(mut self, problems: Vec<String>) -> RBError {
        self.problems = problems;
        self
    }
    pub fn with_exit(mut self, exit_code: i32) -> RBError {
        self.exit_code = exit_code;
        self
    }
    pub fn with_extra(mut self, key: &str, value: Value) -> RBError {
        self.extra.insert(key.to_string(), value);
        self
    }
    pub fn to_value(&self) -> Value {
        let mut d = Dict::new();
        d.insert("code".into(), Value::Str(self.code.clone()));
        d.insert("message".into(), Value::Str(self.message.clone()));
        d.insert("fix".into(), Value::Str(self.fix.clone()));
        if !self.problems.is_empty() {
            d.insert("problems".into(), Value::list_of_str(self.problems.clone()));
        }
        for (k, v) in &self.extra {
            d.insert(k.clone(), v.clone());
        }
        Value::Dict(d)
    }
}

impl std::fmt::Display for RBError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.message)
    }
}

impl std::error::Error for RBError {}

pub type Res<T> = Result<T, RBError>;
