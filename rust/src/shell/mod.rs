//! The shell (SPRINT-OCT24.md section 1): one JSON document in (`rb-shell-view/1.0`), one page out. The renderer is a
//! pure function of the document and reads no ledger, status or file; the model (model.rs) builds the document. rb page
//! and rb export draw it always (BP 2c); rb open's `/` draws it under RB_TEST_SHELL=1 until open_app.html goes (BP 4b).

pub mod model;
pub mod tier;
pub mod views;
pub mod nodes;
pub mod figure;
pub mod strings;
pub mod open;
pub mod history_view;

use crate::errors::{RBError, Res};
use crate::ledger::Ledger;
use crate::places::Chosen;
use crate::value::Value;
use std::path::PathBuf;

/// The exact bytes a page that runs the shell's script (rb page, rb open) inlines between `<script>` and `</script>`; the
/// export, which runs none, names their sha256 so a host may serve these bytes and no others.
pub const SCRIPT: &str = include_str!("assets/script.js");

/// Where a document is drawn: rb page, rb export (inert, no script) or rb open.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Surface {
    Page,
    Export,
    Open,
}

impl Surface {
    /// As the document's `surface` names it.
    pub fn name(self) -> &'static str {
        match self {
            Surface::Page => "page",
            Surface::Export => "export",
            Surface::Open => "open",
        }
    }
}

/// RB_TEST_SHELL=1: rb open's `/` draws the shell (BP 4a's test-only entry, until open_app.html goes at 4b). rb page and
/// rb export draw it whatever this says (BP 2c).
pub fn enabled() -> bool {
    std::env::var("RB_TEST_SHELL").map_or(false, |v| v == "1")
}

/// The document `surface` draws of `led`: the file RB_TEST_SHELL_MODEL names, when a test names one, else the one the
/// model builds, written first to the file RB_TEST_SHELL_DUMP names, when a test names one.
pub fn document(led: &Ledger, surface: Surface, place: Option<&Chosen>) -> Res<Value> {
    if let Some(path) = test_file("RB_TEST_SHELL_MODEL") {
        let text = std::fs::read_to_string(&path).map_err(|e| RBError::could_not_read(&e, &path.display().to_string()))?;
        return crate::pyjson::loads(&text).map_err(|e| RBError::msg("E_OBJECT_INVALID", format!("RB_TEST_SHELL_MODEL holds no JSON document: {e}")));
    }
    let doc = model::build_on(led, surface, place)?;
    if let Some(path) = test_file("RB_TEST_SHELL_DUMP") {
        let text = crate::pyjson::dumps_with(&doc, &crate::pyjson::Dumps { indent: Some(1), ensure_ascii: false, ..Default::default() }).unwrap_or_default();
        std::fs::write(&path, text + "\n").map_err(|e| RBError::could_not_write(&e, &path))?;
    }
    Ok(doc)
}

/// The page `surface` serves of `led`: its document, drawn.
pub fn page(led: &Ledger, surface: Surface, place: Option<&Chosen>) -> Res<String> {
    render(&document(led, surface, place)?, surface)
}

/// One document in, one page out: the frame (top bar, nav, footer) around every view and the cards region. A pure
/// function of `doc`: it reads no ledger, status or file. A page (rb page, rb export) draws only a document built for
/// anyone it may be sent to (tier owner), rb open only the user's own (tier live).
pub fn render(doc: &Value, surface: Surface) -> Res<String> {
    check(doc, surface)?;
    Ok(views::page(doc, surface))
}

/// Why `doc` cannot be drawn on `surface`, if it cannot: not a document of this contract's major version, or a tier
/// the surface never draws.
fn check(doc: &Value, surface: Surface) -> Res<()> {
    if !doc.is_dict() {
        return Err(RBError::msg_fix("E_OBJECT_INVALID", "The shell draws one document, a JSON object; this is not one.", give_one(surface)));
    }
    let contract = text(doc, "contract");
    if !contract.starts_with(MAJOR) {
        let said = if contract.is_empty() { "names no contract".to_string() } else { format!("is {contract}") };
        return Err(RBError::msg_fix("E_OBJECT_INVALID", format!("This document {said}; this rb draws {MAJOR}x."), give_one(surface)));
    }
    let tier = text(doc, "tier");
    let want = tier::Tier::of(surface).name();
    if tier != want {
        let (why, fix) = match surface {
            Surface::Page | Surface::Export => (
                format!(
                    "rb {} draws a document for anyone the page may be sent to (tier owner); this one is tier '{tier}', which holds what only your own screen shows: places, paths and your own words.",
                    surface.name()
                ),
                format!("rb open draws a document of tier live. {}", give_one(surface)),
            ),
            Surface::Open => (
                format!("rb open draws your own screen's document (tier live); this one is tier '{tier}'."),
                format!("rb page and rb export draw a document of tier owner. {}", give_one(surface)),
            ),
        };
        return Err(RBError::msg_fix("E_OBJECT_INVALID", why, fix));
    }
    Ok(())
}

/// What to do when a test's document (RB_TEST_SHELL_MODEL, the only way to hand the shell one) cannot be drawn.
fn give_one(surface: Surface) -> String {
    format!("Give RB_TEST_SHELL_MODEL a document this rb builds for rb {}: RB_TEST_SHELL_DUMP=<file> writes it.", surface.name())
}

/// The contract's major version, as `contract` starts.
const MAJOR: &str = "rb-shell-view/1.";

/// The text at a dotted path of `v`, or "" where the path is absent or holds no text: an absent optional field is drawn
/// as nothing (SPRINT-OCT24.md section 1.8).
pub(crate) fn text<'a>(v: &'a Value, path: &str) -> &'a str {
    at(v, path).and_then(Value::as_str).unwrap_or("")
}

/// The value at a dotted path of `v`, if every step is there.
pub(crate) fn at<'a>(v: &'a Value, path: &str) -> Option<&'a Value> {
    path.split('.').try_fold(v, |v, k| v.get(k)).filter(|v| !v.is_null())
}

/// The list at a dotted path of `v`, empty where it is absent.
pub(crate) fn list<'a>(v: &'a Value, path: &str) -> &'a [Value] {
    at(v, path).and_then(Value::as_list).map_or(&[], Vec::as_slice)
}

/// A whole number at a dotted path of `v`, if one is there.
pub(crate) fn count(v: &Value, path: &str) -> Option<i64> {
    whole(at(v, path)?)
}

/// `v` as a whole number, if it is one.
pub(crate) fn whole(v: &Value) -> Option<i64> {
    match v {
        Value::Float(f) if f.fract() == 0.0 => Some(*f as i64),
        Value::Bool(_) => None,
        n => n.as_i64(),
    }
}

/// Text as HTML draws it, in an element or an attribute: `& < > " '` escaped, nothing else changed.
pub(crate) fn esc(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#x27;"),
            _ => out.push(c),
        }
    }
    out
}

fn test_file(var: &str) -> Option<PathBuf> {
    std::env::var_os(var).filter(|v| !v.is_empty()).map(PathBuf::from)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn doc(contract: &str, tier: &str) -> Value {
        crate::pyjson::loads(&format!("{{\"contract\": \"{contract}\", \"tier\": \"{tier}\", \"project\": {{\"id\": \"int8-resnet50\", \"title\": \"INT8 ResNet-50 on ImageNet\"}}}}")).unwrap()
    }

    /// A page draws only the Owner tier and rb open only the Live one; a document of another major version, or one
    /// that is not an object, is never drawn; a minor version is.
    #[test]
    fn each_surface_draws_only_its_tier() {
        for (surface, ok, refused) in [(Surface::Page, "owner", "live"), (Surface::Export, "owner", "live"), (Surface::Open, "live", "owner")] {
            assert!(render(&doc("rb-shell-view/1.0", ok), surface).is_ok(), "{surface:?}");
            assert!(render(&doc("rb-shell-view/1.3", ok), surface).is_ok(), "{surface:?}");
            let e = render(&doc("rb-shell-view/1.0", refused), surface).unwrap_err();
            assert!(e.to_string().contains(&format!("tier '{refused}'")), "{e}");
            assert!(render(&doc("rb-shell-view/2.0", ok), surface).is_err());
            assert!(render(&doc("rb-shell-view/10.0", ok), surface).is_err());
        }
        assert!(render(&Value::List(Vec::new()), Surface::Page).is_err());
    }

    /// The Live document's places never reach a page, whatever a page is given: a page refuses it before drawing.
    #[test]
    fn a_page_draws_nothing_of_a_live_document() {
        let mut live = doc("rb-shell-view/1.0", "live");
        live.as_dict_mut().unwrap().insert("places".into(), crate::pyjson::loads("{\"place\": {\"path\": \"/home/ana/int8-resnet50\"}}").unwrap());
        assert!(render(&live, Surface::Page).is_err() && render(&live, Surface::Export).is_err());
    }
}
