//! Environment receipts: a fingerprint of the machine every run's receipt carries. The Python side of a project is
//! what the runs ran in, so the fingerprint is the one the project's Python gives (its version, the platform, and the
//! versions of torch, numpy, jax, tensorflow and pydantic installed there), asked once and remembered by the
//! interpreter's path and the state of its site-packages.

use crate::value::{Dict, Value};
use std::path::{Path, PathBuf};
use std::time::Duration;

pub const PACKAGES: &[&str] = &["torch", "numpy", "jax", "tensorflow", "pydantic"];

const SCRIPT: &str = r#"
import json, platform, site, sys
from importlib import metadata
fp = {"python": platform.python_version(), "platform": platform.platform()}
pk = {}
for name in ("torch", "numpy", "jax", "tensorflow", "pydantic"):
    try:
        pk[name] = metadata.version(name)
    except Exception:
        pass
dirs = []
try:
    dirs = list(site.getsitepackages()) + [site.getusersitepackages()]
except Exception:
    pass
print(json.dumps({"fp": fp, "packages": pk, "dirs": dirs}))
"#;

fn mtime_ns(p: &Path) -> i64 {
    std::fs::metadata(p).map(|m| crate::sys::mtime_ns(&m) as i64).unwrap_or(0)
}

fn stamp(python: &Path, dirs: &[String]) -> Value {
    let mut parts = vec![Value::Str(python.to_string_lossy().into_owned()), Value::Int(mtime_ns(python))];
    for d in dirs {
        parts.push(Value::Str(d.clone()));
        parts.push(Value::Int(mtime_ns(Path::new(d))));
    }
    Value::List(parts)
}

fn ask(python: &Path) -> Option<Value> {
    let r = crate::sources::run(&python.to_string_lossy(), &["-c", SCRIPT], Some(&crate::sys::neutral_dir()), &[], None, Duration::from_secs(60))?;
    if r.code != 0 {
        return None;
    }
    crate::pyjson::loads(String::from_utf8_lossy(&r.stdout).trim()).ok()
}

/// The fingerprint, in its stored key order: python, platform, rb, then the packages it found.
pub fn fingerprint(cache_dir: Option<&Path>) -> Dict {
    let mut fp = Dict::new();
    if let Some(Value::Dict(given)) = std::env::var("RB_TEST_ENV").ok().filter(|t| !t.is_empty()).and_then(|t| crate::pyjson::loads(&t).ok()) {
        // the tests pin the environment, as RB_TEST_NOW pins the clock: no interpreter is asked
        let text = |k: &str| Value::Str(given.get(k).map(crate::pyjson::py_str).unwrap_or_default());
        fp.insert("python".into(), text("python"));
        fp.insert("platform".into(), text("platform"));
        fp.insert("rb".into(), Value::Str(crate::version().into()));
        for (k, v) in &given {
            if !matches!(k.as_str(), "python" | "platform" | "rb") {
                fp.insert(k.clone(), Value::Str(crate::pyjson::py_str(v)));
            }
        }
        return fp;
    }
    let python: Option<PathBuf> = crate::cli::python();
    let answer: Option<Value> = python.as_ref().and_then(|py| {
        let cache = cache_dir.map(|d| d.join("environment.json"));
        if let Some(c) = &cache {
            if let Ok(text) = std::fs::read_to_string(c) {
                if let Ok(v) = crate::pyjson::loads(&text) {
                    let dirs: Vec<String> = v.get("dirs").and_then(|d| d.as_list()).map(|l| l.iter().filter_map(|x| x.as_str().map(|s| s.to_string())).collect()).unwrap_or_default();
                    if v.get("stamp") == Some(&stamp(py, &dirs)) {
                        return Some(v);
                    }
                }
            }
        }
        let mut v = ask(py)?;
        let dirs: Vec<String> = v.get("dirs").and_then(|d| d.as_list()).map(|l| l.iter().filter_map(|x| x.as_str().map(|s| s.to_string())).collect()).unwrap_or_default();
        if let Value::Dict(d) = &mut v {
            d.insert("stamp".into(), stamp(py, &dirs));
        }
        // a write rb only previews keeps nothing, a cache included
        if let Some(c) = cache.as_ref().filter(|_| !crate::dry::active()) {
            if let Some(parent) = c.parent() {
                if parent.exists() || std::fs::create_dir_all(parent).is_ok() {
                    let _ = std::fs::write(c, crate::pyjson::dumps(&v));
                }
            }
        }
        Some(v)
    });
    match &answer {
        Some(a) => {
            if let Some(Value::Dict(f)) = a.get("fp") {
                for (k, v) in f {
                    fp.insert(k.clone(), v.clone());
                }
            }
            fp.insert("rb".into(), Value::Str(crate::version().into()));
            if let Some(Value::Dict(p)) = a.get("packages") {
                for (k, v) in p {
                    fp.insert(k.clone(), v.clone());
                }
            }
        }
        None => {
            fp.insert("platform".into(), Value::Str(crate::sys::platform()));
            fp.insert("rb".into(), Value::Str(crate::version().into()));
        }
    }
    if let Ok(cuda) = std::env::var("RB_CUDA") {
        // the SDK passes torch's CUDA version when the recording process has torch loaded
        if !cuda.is_empty() {
            fp.insert("cuda".into(), Value::Str(cuda));
        }
    }
    fp
}
