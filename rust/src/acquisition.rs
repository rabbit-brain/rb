//! How a resource was acquired, computed on read from its recorded recipe (BP 1c) and never stored: the command that
//! gets it again, and the file and line it was taken from. rb keeps a recipe as given (`rb resource add --recipe`); one
//! that ends in its citation, `<command> (<file>:<line>)`, is handed apart here so no surface splits it. A resource's
//! role is what the runs and analyses that read it read it as, in plain words, worked out on read as well. Track P;
//! shell/model.rs puts `fields` on resources{}.

use crate::errors::Res;
use crate::ledger::Ledger;
use crate::record::{Analysis, Resource, Run};
use crate::value::{Dict, Value};
use indexmap::IndexMap;
use regex::Regex;
use std::sync::OnceLock;

/// A recipe handed apart: `command` as given, without the citation after it; `from` the citation, else where the
/// resource comes from as recorded (`--source`), else "".
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Acquired {
    pub command: String,
    pub from: String,
}

/// A citation a recipe may end in: a file with a line (`scripts/README.md:5`, `build_calib.py:7, :12-14`) or a key
/// (`configs/data.yaml#paths.val`), in parentheses after the command; a file is named with a folder or an extension
/// (`names_a_file`), so `(v1:2)` and `(127.0.0.1:8000)` name none. Anything else in parentheses is the command's.
fn cited() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^(?s)(.*\S)\s+\(([^\s()#:]+(?::\d+(?:-\d+)?(?:,\s*:\d+(?:-\d+)?)*|#[^\s()]+))\)\s*$").unwrap())
}

/// Whether a citation's first part names a file: a path with a folder, a name with an extension that starts with a
/// letter (`README.md`, never an address such as `127.0.0.1`), or a file named without one by convention.
fn names_a_file(file: &str) -> bool {
    const BARE: &[&str] = &["Makefile", "GNUmakefile", "Dockerfile", "Justfile", "README", "LICENSE"];
    file.contains('/') || BARE.contains(&file) || file.rsplit_once('.').map_or(false, |(stem, ext)| !stem.is_empty() && ext.starts_with(|c: char| c.is_ascii_alphabetic()))
}

/// The command and the citation of a recipe as rb keeps it; a recipe that names no file and line after it is all command.
pub fn split(recipe: &str) -> (String, String) {
    let recipe = recipe.trim();
    match cited().captures(recipe) {
        Some(c) if c[2].split([':', '#']).next().map_or(false, names_a_file) => (c[1].to_string(), c[2].to_string()),
        _ => (recipe.to_string(), String::new()),
    }
}

/// How `res` is got again, from its recipe: None when it records no recipe (a resource with only an address or a
/// source has no command to give).
pub fn of(res: &Resource) -> Option<Acquired> {
    let a = res.acquisition.as_ref()?;
    let recipe = a.recipe.as_deref().map(str::trim).filter(|r| !r.is_empty())?;
    let (command, from) = split(recipe);
    let from = if from.is_empty() { a.source.trim().to_string() } else { from };
    Some(Acquired { command, from })
}

/// An input's role in plain words: "training data", "evaluation data" for rb's train and eval, in any case; any other
/// as recorded.
pub fn role_words(role: &str) -> String {
    match role.trim().to_lowercase().as_str() {
        "train" | "training" => "training data".into(),
        "eval" | "evaluation" => "evaluation data".into(),
        _ => role.trim().to_string(),
    }
}

/// What each resource was read as, by resource id, in plain words: each role a standing run or analysis read it in
/// ("training data", then "evaluation data", then any other in the order the record first gives it, by when each run or
/// analysis was recorded), as one list
/// ("training data and evaluation data"). A resource nothing standing reads (one a run only made) has none here.
pub fn roles(led: &Ledger) -> Res<IndexMap<String, String>> {
    let runs = led.all::<Run>()?;
    let analyses = led.all::<Analysis>()?;
    // an experiment taken back reads nothing; a run taken back in part still stands, as Reproduce reads it, and one
    // taken back whole reads nothing
    let gone: std::collections::HashSet<String> = led.all::<crate::record::Experiment>()?.iter().filter(|e| e.retracted.is_some()).map(|e| e.id.clone()).collect();
    let mut read: Vec<(&str, &str, &crate::record::ResourceUse)> = Vec::new();
    for r in runs.iter().filter(|r| !gone.contains(&r.experiment) && r.retracted.as_ref().map_or(true, |x| !x.scope.is_empty())) {
        read.extend(r.inputs.iter().map(|u| (r.created_at.as_str(), r.id.as_str(), u)));
    }
    for a in analyses.iter().filter(|a| !gone.contains(&a.experiment) && a.retracted.is_none()) {
        read.extend(a.inputs.iter().map(|u| (a.created_at.as_str(), a.id.as_str(), u)));
    }
    // ids are drawn at random: the record's order is when each was recorded
    read.sort_by(|x, y| (x.0, x.1).cmp(&(y.0, y.1)));
    let mut by: IndexMap<String, Vec<String>> = IndexMap::new();
    for (_, _, u) in read {
        let words = role_words(&u.role);
        let seen = by.entry(u.resource.clone()).or_default();
        if !words.is_empty() && !seen.contains(&words) {
            seen.push(words);
        }
    }
    let rank = |w: &String| match w.as_str() {
        "training data" => 0,
        "evaluation data" => 1,
        _ => 2,
    };
    Ok(by
        .into_iter()
        .filter(|(_, w)| !w.is_empty())
        .map(|(id, mut w)| {
            w.sort_by_key(rank);
            (id, crate::next_step::and_list(&w))
        })
        .collect())
}

/// The fields a resource's view adds, by resource id, for every resource of the record: `role`, what it was read as
/// ("" when nothing standing reads it), and `acquisition`, its recipe handed apart ({command, from}, or null when it
/// records none). Both computed on read, never stored.
pub fn fields(led: &Ledger) -> Res<IndexMap<String, Dict>> {
    let roles = roles(led)?;
    Ok(led
        .all::<Resource>()?
        .iter()
        .map(|res| {
            let acquisition = of(res).map_or(Value::Null, |a| crate::obj! {"command" => a.command, "from" => a.from});
            let d = crate::dict! {"role" => roles.get(&res.id).cloned().unwrap_or_default(), "acquisition" => acquisition};
            (res.id.clone(), d)
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use super::{role_words, split};

    #[test]
    fn a_recipe_hands_its_citation_apart_only_when_it_names_a_file() {
        assert_eq!(split("python get_data.py --split val (scripts/README.md:5)"), ("python get_data.py --split val".into(), "scripts/README.md:5".into()));
        assert_eq!(split("python scripts/build_calib.py --out data/calib (scripts/build_calib.py:7, :12-14)"), ("python scripts/build_calib.py --out data/calib".into(), "scripts/build_calib.py:7, :12-14".into()));
        assert_eq!(split("make calib (configs/data.yaml#paths.calib)"), ("make calib".into(), "configs/data.yaml#paths.calib".into()));
        assert_eq!(split("make data (Makefile:3)"), ("make data".into(), "Makefile:3".into()));
        // words in parentheses, a subshell, a bare file and no space before the parenthesis are the command's own
        for whole in ["download it from the lab share (ask ana for access)", "(cd data && python get.py)", "python get.py (README.md)", "f(README.md:3)", "make data (v1:2)", "python get.py (python:3)", "python -m http.server (127.0.0.1:8000)"] {
            assert_eq!(split(whole), (whole.to_string(), String::new()), "{whole}");
        }
        // a recipe of several lines keeps them, and its citation is still the last thing in it
        assert_eq!(split("python a.py\npython b.py (README.md:9)"), ("python a.py\npython b.py".into(), "README.md:9".into()));
        assert_eq!(split("  python get.py  "), ("python get.py".into(), String::new()));
    }

    #[test]
    fn a_role_reads_in_plain_words() {
        for (role, words) in [("train", "training data"), ("training", "training data"), ("eval", "evaluation data"), ("evaluation", "evaluation data"), ("Eval", "evaluation data"), (" TRAIN ", "training data"), ("weights", "weights"), ("test", "test")] {
            assert_eq!(role_words(role), words);
        }
    }
}
