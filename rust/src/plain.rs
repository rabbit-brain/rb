//! Plain record text. rb shows the text anyone writes in the record exactly as written, on every screen, and renders
//! no markup, so LaTeX copied from a paper reads there as its backslashes and dollar signs. rb never changes or refuses
//! text for it: when an agent's write adds text that holds LaTeX, one line after the write names the field and says
//! what to write instead (`data.warnings` in --json and over MCP, a warning from the SDK). A human's writes get none.
//!
//! What counts is kept narrow, so nothing else is taken for LaTeX: a backslash command a paper uses (`\emph{`,
//! `\cite`, `\ref{`, `\Delta`), a `9.9\%`, or `$...$` around math that holds a command, a `^` or a `_`. A code span, a
//! path, an address, a regex, a shell or Make variable and a price (`$5`) never count, and neither do the fields that
//! keep text exactly as it was given or found: the user's words, a quote of a file, a command as given.

use crate::value::{Dict, Value};
use std::cell::RefCell;

/// What a mark is, which decides what to write instead.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    /// `$x^2$`, `\Delta`, `\frac{a}{b}`, `9.9\%`
    Math,
    /// a command around words: `\emph{fast}`, `\texttt{lr}`
    Format,
    /// `\cite{key}`
    Cite,
    /// a reference to a table, figure, section or equation: `\ref{tab:main}`
    Ref,
    /// a command a paper defines for itself: `\ourmodel{}`
    Macro,
}

/// One piece of LaTeX in a text, as it is written there (clipped when long).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Mark {
    pub shown: String,
    pub kind: Kind,
}

const CITES: &[&str] = &[
    "cite", "citep", "citet", "citealp", "citealt", "citeauthor", "citeyear", "citeyearpar", "citenum", "Cite", "Citep", "Citet", "parencite",
    "textcite", "autocite", "footcite", "nocite",
];
const REFS: &[&str] = &["ref", "eqref", "autoref", "cref", "Cref", "pageref", "nameref", "vref", "label"];
const FORMATS: &[&str] = &[
    "emph", "textit", "textbf", "texttt", "textsc", "textsf", "textrm", "textup", "textsl", "textmd", "text", "underline", "mathrm", "mathbf",
    "mathit", "mathsf", "mathtt", "url", "href", "footnote", "mbox", "hbox", "textsuperscript", "textsubscript", "section", "subsection",
    "subsubsection", "paragraph", "caption", "begin", "end", "textcolor", "colorbox",
];
const MATH_ARGS: &[&str] = &[
    "frac", "dfrac", "tfrac", "sqrt", "hat", "widehat", "bar", "overline", "tilde", "widetilde", "vec", "dot", "ddot", "mathcal", "mathbb",
    "mathfrak", "boldsymbol", "bm", "operatorname",
];
const SYMBOLS: &[&str] = &[
    "alpha", "beta", "gamma", "Gamma", "delta", "Delta", "epsilon", "varepsilon", "zeta", "eta", "theta", "Theta", "vartheta", "iota", "kappa",
    "lambda", "Lambda", "mu", "nu", "xi", "Xi", "pi", "Pi", "varpi", "rho", "varrho", "sigma", "Sigma", "varsigma", "tau", "upsilon", "Upsilon",
    "phi", "Phi", "varphi", "chi", "psi", "Psi", "omega", "Omega", "times", "cdot", "pm", "mp", "div", "leq", "geq", "le", "ge", "neq", "ne",
    "approx", "sim", "simeq", "propto", "equiv", "ll", "gg", "infty", "partial", "nabla", "ell", "to", "rightarrow", "leftarrow", "Rightarrow",
    "Leftarrow", "leftrightarrow", "mapsto", "in", "notin", "subset", "subseteq", "cup", "cap", "ldots", "cdots", "dots", "circ", "sum", "prod",
];
/// How much of a mark is shown before it is clipped.
const SHOWN: usize = 32;

/// The LaTeX in `text`, each mark once, in the order it is written.
pub fn marks(text: &str) -> Vec<Mark> {
    let c: Vec<char> = text.chars().collect();
    let skip = not_markup(&c);
    let mut found: Vec<(usize, Mark)> = Vec::new();
    let mut in_math = vec![false; c.len()];
    let mut i = 0;
    while i < c.len() {
        if skip[i] || c[i] != '$' || escaped(&c, i) {
            i += 1;
            continue;
        }
        match math_span(&c, &skip, i) {
            Some(end) => {
                let inner = if c[i + 1] == '$' { &c[i + 2..end - 2] } else { &c[i + 1..end - 1] };
                if mathy(inner) {
                    in_math[i..end].iter_mut().for_each(|m| *m = true);
                    found.push((i, Mark { shown: clip(&c[i..end]), kind: Kind::Math }));
                }
                i = end;
            }
            None => i += 1,
        }
    }
    for i in 0..c.len() {
        if skip[i] || in_math[i] || c[i] != '\\' || escaped(&c, i) {
            continue;
        }
        if let Some(m) = command(&c, i) {
            found.push((i, m));
        }
    }
    found.sort_by_key(|(at, _)| *at);
    let mut out: Vec<Mark> = Vec::new();
    for (_, m) in found {
        if !out.contains(&m) {
            out.push(m);
        }
    }
    out
}

fn escaped(c: &[char], i: usize) -> bool {
    i > 0 && c[i - 1] == '\\'
}

fn clip(c: &[char]) -> String {
    if c.len() <= SHOWN {
        c.iter().collect()
    } else {
        c[..SHOWN - 1].iter().chain(std::iter::once(&'…')).collect()
    }
}

/// What in the text is never LaTeX, whatever it holds: code spans, and the words that are an address, a path or a regex.
fn not_markup(c: &[char]) -> Vec<bool> {
    let n = c.len();
    let mut skip = vec![false; n];
    // a code span: a run of backticks, the same run again
    let mut i = 0;
    while i < n {
        if c[i] != '`' {
            i += 1;
            continue;
        }
        let run = c[i..].iter().take_while(|x| **x == '`').count();
        let mut j = i + run;
        let mut close = None;
        while j < n {
            if c[j] == '`' {
                let r = c[j..].iter().take_while(|x| **x == '`').count();
                if r == run {
                    close = Some(j);
                    break;
                }
                j += r;
            } else {
                j += 1;
            }
        }
        match close {
            Some(j) => {
                skip[i..j + run].iter_mut().for_each(|s| *s = true);
                i = j + run;
            }
            None => i += run, // a backtick left open starts no code span
        }
    }
    let mut s = 0;
    while s < n {
        if c[s].is_whitespace() {
            s += 1;
            continue;
        }
        let e = s + c[s..].iter().take_while(|x| !x.is_whitespace()).count();
        if not_words(&c[s..e]) {
            skip[s..e].iter_mut().for_each(|x| *x = true);
        }
        s = e;
    }
    skip
}

/// A word that is an address, a path or a regex, which may hold a backslash or a dollar sign and is no LaTeX.
fn not_words(w: &[char]) -> bool {
    let lead = w.iter().take_while(|x| "\"'([<".contains(**x)).count();
    let t = &w[lead..];
    let s: String = t.iter().collect();
    if s.contains("://") || s.starts_with("www.") {
        return true;
    }
    // a Windows path: from a drive, a share, here, home or a variable
    if t.len() >= 3 && t[0].is_ascii_alphabetic() && t[1] == ':' && (t[2] == '\\' || t[2] == '/') {
        return true;
    }
    if s.starts_with("\\\\") && t.get(2).map_or(false, |x| x.is_alphanumeric()) {
        return true;
    }
    if s.starts_with(".\\") || s.starts_with("..\\") || s.starts_with("~\\") || (s.starts_with('%') && s[1..].contains("%\\")) {
        return true;
    }
    // a regex: an escape only a regex uses (\d, \s+, \b, \.), or a group or class a regex opens
    if s.contains("(?") || s.contains("[^") {
        return true;
    }
    let regex_escape = (0..t.len().saturating_sub(1)).any(|k| {
        t[k] == '\\'
            && (".()[]+*?|/^".contains(t[k + 1]) || ("dDwWsSbBAzZGK".contains(t[k + 1]) && t.get(k + 2).map_or(true, |x| !x.is_ascii_alphabetic())))
    });
    if regex_escape {
        return true;
    }
    // a path between names (out\run1\metrics.json), with none of the braces, dollars or carets LaTeX would bring
    let pathy = |x: char| x.is_alphanumeric() || "_.-~%".contains(x);
    !t.iter().any(|x| "{}$^".contains(*x)) && (1..t.len().saturating_sub(1)).any(|k| t[k] == '\\' && pathy(t[k - 1]) && pathy(t[k + 1]))
}

/// Where the math that opens at `i` ends (past its closing dollar), or None when these dollars delimit no math: as
/// Markdown and Pandoc read them, an opening `$` is followed by a non-space, a closing one follows a non-space and is
/// not followed by a digit (`$5 and $10`), nor here by a letter or a variable's brace (`$HOME/$USER`, `${OUT}/${RUN}`,
/// `$(OUT)/$(RUN)`); nor does a shell or Make variable open math (`$(OUT_DIR)/$@`).
fn math_span(c: &[char], skip: &[bool], i: usize) -> Option<usize> {
    let n = c.len();
    let unmasked = |a: usize, b: usize| skip[a..b].iter().all(|s| !s);
    if c.get(i + 1) == Some(&'$') {
        let mut j = i + 2;
        while j + 1 < n {
            if c[j] == '$' && !escaped(c, j) {
                return (c[j + 1] == '$' && j > i + 2 && j - i <= 300 && unmasked(i, j + 2)).then_some(j + 2);
            }
            j += 1;
        }
        return None;
    }
    let open = i + 1;
    if open >= n || c[open].is_whitespace() || variable(c, i) {
        return None;
    }
    let j = open + c[open..].iter().take_while(|x| **x != '$').count();
    if j >= n || escaped(c, j) || c[open..j].contains(&'\n') {
        return None;
    }
    let closes = !c[j - 1].is_whitespace() && c.get(j + 1).map_or(true, |x| !(x.is_alphanumeric() || "_{(".contains(*x)));
    (closes && j - i <= 300 && unmasked(i, j + 1)).then_some(j + 1)
}

/// `${NAME}` or `$(NAME)` at `i`: a shell or Make variable. Math in braces holds more than a name (`${\cal O}(n)$`).
fn variable(c: &[char], i: usize) -> bool {
    let close = match c.get(i + 1) {
        Some('{') => '}',
        Some('(') => ')',
        _ => return false,
    };
    let rest = c.get(i + 2..).unwrap_or(&[]);
    let name = rest.iter().take_while(|x| x.is_ascii_alphanumeric() || **x == '_').count();
    name > 0 && !rest[0].is_ascii_digit() && rest.get(name) == Some(&close)
}

/// Whether what sits between dollars is math as LaTeX writes it: a command, a superscript, or a subscript (after a
/// letter or a bracket, never a digit: `$5_000`), its base outside the dollars too (`R$^2$`).
fn mathy(m: &[char]) -> bool {
    let n = m.len();
    (0..n).any(|k| match m[k] {
        '\\' => (k == 0 || m[k - 1] != '\\') && m[k + 1..].iter().take_while(|x| x.is_ascii_alphabetic()).count() >= 2,
        '^' => k + 1 < n && (k == 0 || m[k - 1].is_alphanumeric() || ")]}".contains(m[k - 1])) && (m[k + 1].is_alphanumeric() || "{\\+-(*'".contains(m[k + 1])),
        '_' => k + 1 < n && (k == 0 || m[k - 1].is_alphabetic() || ")]}".contains(m[k - 1])) && (m[k + 1].is_alphanumeric() || "{\\".contains(m[k + 1])),
        _ => false,
    })
}

/// The command at `i` (a backslash), when it is one a paper writes.
fn command(c: &[char], i: usize) -> Option<Mark> {
    let name: String = c[i + 1..].iter().take_while(|x| x.is_ascii_alphabetic()).collect();
    if name.is_empty() {
        // 9.9\%: a percent sign as LaTeX escapes it
        return (c.get(i + 1) == Some(&'%') && i > 0 && c[i - 1].is_ascii_digit()).then(|| Mark { shown: "\\%".into(), kind: Kind::Math });
    }
    let mut at = i + 1 + name.len();
    if c.get(at) == Some(&'*') {
        at += 1;
    }
    let arg = matches!(c.get(at), Some('{') | Some('['));
    let brace = c.get(at) == Some(&'{');
    let n = name.as_str();
    let kind = if CITES.contains(&n) {
        Kind::Cite
    } else if REFS.contains(&n) && brace {
        Kind::Ref
    } else if FORMATS.contains(&n) && arg {
        Kind::Format
    } else if (MATH_ARGS.contains(&n) && arg) || SYMBOLS.contains(&n) {
        Kind::Math
    } else if name.len() >= 2 && brace && !quantifier(&c[at..]) {
        Kind::Macro
    } else {
        return None;
    };
    Some(Mark { shown: shown_command(c, i, at, &name), kind })
}

/// `{2}`, `{2,}`, `{2,5}`: a regex's count, never a command's argument.
fn quantifier(c: &[char]) -> bool {
    let body: Vec<&char> = c[1..].iter().take_while(|x| **x != '}').collect();
    c.len() > body.len() + 1 && body.first().map_or(false, |x| x.is_ascii_digit()) && body.iter().all(|x| x.is_ascii_digit() || **x == ',')
}

/// A command as written, with its arguments when they are short: `\emph{fast}`, else `\emph{…}`.
fn shown_command(c: &[char], i: usize, at: usize, name: &str) -> String {
    let mut end = at;
    if c.get(end) == Some(&'[') {
        end += c[end..].iter().take_while(|x| **x != ']').count() + 1;
    }
    if c.get(end) == Some(&'{') {
        let mut depth = 0;
        let mut k = end;
        while k < c.len() {
            match c[k] {
                '{' => depth += 1,
                '}' => {
                    depth -= 1;
                    if depth == 0 {
                        break;
                    }
                }
                _ => {}
            }
            k += 1;
        }
        end = (k + 1).min(c.len());
    }
    let end = end.min(c.len());
    if end <= at {
        return format!("\\{name}");
    }
    if end - i <= SHOWN && !c[i..end].contains(&'\n') {
        c[i..end].iter().collect()
    } else {
        format!("\\{name}{{…}}")
    }
}

// ---------------------------------------------------------------- the text a write adds

/// The fields of a record that keep its author's own text, by the record's kind, as paths into its stored form (a part
/// is a key, `[]` each item of a list), each with the name a writer gives it by. Left out on purpose: the user's
/// words, quotes of files (a source's quote and the line rb read, a results folder's quotes), commands as given (a
/// receipt's command, a resource's recipe), values, paths, addresses, names and ids, a link's copy of its question,
/// and what rb words itself (an amendment's change, a conflict).
const PROSE: &[(&str, &str, &str)] = &[
    ("project", "title", "title"),
    ("project", "notes[].text", "text"),
    ("question", "text", "text"),
    ("question", "retracted.why", "why"),
    ("note", "text", "text"),
    ("experiment", "title", "title"),
    ("experiment", "purpose", "purpose"),
    ("experiment", "hypothesis", "hypothesis"),
    ("experiment", "why", "why"),
    ("experiment", "if_holds", "if_holds"),
    ("experiment", "if_not", "if_not"),
    ("experiment", "retracted.why", "why"),
    ("experiment", "frozen.why", "why"),
    ("experiment", "amendments[].why", "why"),
    ("experiment", "relations[].note", "note"),
    ("experiment", "settings[].note", "note"),
    ("experiment", "settings[].source.note", "source"),
    ("experiment", "settings[].source.locator", "locator"),
    ("experiment", "settings[].cited.source.note", "cited_source"),
    ("experiment", "settings[].retracted.why", "why"),
    ("experiment", "notes[].text", "text"),
    ("experiment", "variants[].note", "note"),
    ("experiment", "variants[].retracted.why", "why"),
    ("experiment", "variants[].settings[].note", "note"),
    ("experiment", "variants[].settings[].source.note", "source"),
    ("experiment", "variants[].settings[].source.locator", "locator"),
    ("experiment", "variants[].settings[].cited.source.note", "cited_source"),
    ("experiment", "variants[].settings[].retracted.why", "why"),
    ("variant", "note", "note"),
    ("variant", "retracted.why", "why"),
    ("variant", "settings[].note", "note"),
    ("variant", "settings[].source.note", "source"),
    ("variant", "settings[].source.locator", "locator"),
    ("variant", "settings[].cited.source.note", "cited_source"),
    ("variant", "settings[].retracted.why", "why"),
    ("criterion", "statement", "statement"),
    ("criterion", "note", "note"),
    ("criterion", "source.note", "source"),
    ("criterion", "source.locator", "locator"),
    ("criterion", "retracted.why", "why"),
    ("criterion", "question_links[].when_met", "when_met"),
    ("criterion", "question_links[].why", "why"),
    ("criterion", "question_links[].retracted.why", "why"),
    ("criterion", "relations[].why", "why"),
    ("criterion", "relations[].retracted.why", "why"),
    ("run", "note", "note"),
    ("run", "retracted.why", "why"),
    ("analysis", "title", "title"),
    ("analysis", "method", "method"),
    ("analysis", "why", "why"),
    ("analysis", "note", "note"),
    ("analysis", "comparisons[].note", "note"),
    ("analysis", "source.locator", "locator"),
    ("analysis", "retracted.why", "why"),
    ("metric", "description", "description"),
    ("metric", "retracted.why", "why"),
    ("resource", "name", "name"),
    ("resource", "subset", "subset"),
    ("resource", "note", "note"),
    ("resource", "acquisition.source", "source"),
    ("resource", "locations[].note", "note"),
    ("resource", "exposures[].why", "why"),
    ("resource", "retracted.why", "why"),
    ("decision", "why", "why"),
    ("decision", "revisit", "revisit"),
    ("decision", "retracted.why", "why"),
];

/// The writes whose text is not their writer's own: a clone's, from its bundle; a results folder's, from its files; a
/// record put back as rb wrote it.
pub const COPIED: &[&str] = &["clone", "reconcile", "reindex", "restore", "reid"];

/// A record's author text: (the field, its text), every one that is not empty.
pub fn prose(kind: &str, form: &Dict) -> Vec<(&'static str, String)> {
    fn walk(v: &Value, path: &[&str], field: &'static str, out: &mut Vec<(&'static str, String)>) {
        let Some((head, rest)) = path.split_first() else {
            if let Value::Str(s) = v {
                if !s.trim().is_empty() {
                    out.push((field, s.clone()));
                }
            }
            return;
        };
        let (key, each) = match head.strip_suffix("[]") {
            Some(k) => (k, true),
            None => (*head, false),
        };
        let Value::Dict(d) = v else { return };
        match (d.get(key), each) {
            (Some(Value::List(items)), true) => items.iter().for_each(|x| walk(x, rest, field, out)),
            (Some(x), false) => walk(x, rest, field, out),
            _ => {}
        }
    }
    let mut out = Vec::new();
    let form = Value::Dict(form.clone());
    for (_, path, field) in PROSE.iter().filter(|(k, _, _)| *k == kind) {
        walk(&form, &path.split('.').collect::<Vec<_>>(), field, &mut out);
    }
    out
}

/// What this command's writes leave for the lines after them: the author text an agent's writes added, as (field,
/// text), with the ids that writer goes by (its runtime's and its model's), and the lines a write tells beside its
/// record (`tell`).
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Kept {
    pub added: Vec<(&'static str, String)>,
    pub writer: Vec<String>,
    pub told: Vec<String>,
}

thread_local! {
    static KEPT: RefCell<Kept> = const { RefCell::new(Kept { added: Vec::new(), writer: Vec::new(), told: Vec::new() }) };
}

/// Keep the text an agent's write adds: what `after` holds that `before` did not (as many times as it holds it), with
/// the ids its writer goes by.
pub fn added(agent: bool, op: &str, before: &[(&'static str, String)], after: &[(&'static str, String)], writer: &[String]) {
    if !agent || COPIED.contains(&op) {
        return;
    }
    let mut had: Vec<&(&'static str, String)> = before.iter().collect();
    KEPT.with(|k| {
        let mut k = k.borrow_mut();
        for id in writer {
            if !k.writer.contains(id) {
                k.writer.push(id.clone());
            }
        }
        for t in after {
            match had.iter().position(|h| *h == t) {
                Some(i) => {
                    had.swap_remove(i);
                }
                None if !k.added.contains(t) => k.added.push(t.clone()),
                None => {}
            }
        }
    });
}

/// Text a write took from the record as it was (an experiment's settings, copied with --like), or from a file it
/// hashed (the notes in an analysis's output file), is not the writer's.
pub fn copied(texts: &[(&'static str, String)]) {
    KEPT.with(|k| k.borrow_mut().added.retain(|t| !texts.contains(t)));
}

/// A line a write says beside its record, after the command's own text: in --json and over MCP among data.warnings,
/// and from Python as an RBWarning. Each is told once.
pub fn tell(line: String) {
    KEPT.with(|k| {
        let mut k = k.borrow_mut();
        if !k.told.contains(&line) {
            k.told.push(line);
        }
    });
}

/// What an enclosing command has gathered, set aside while a command line runs inside it.
pub fn set_aside() -> Kept {
    KEPT.with(|k| std::mem::take(&mut *k.borrow_mut()))
}

pub fn put_back(kept: Kept) {
    KEPT.with(|k| *k.borrow_mut() = kept);
}

/// The lines after this command's writes: the LaTeX in the text they added, the words in it about who wrote it, then
/// each line a write told. What they added is forgotten.
pub fn take_note() -> Vec<String> {
    let k = set_aside();
    note(&k.added).into_iter().chain(writer_note(&k.added, &k.writer)).chain(attribution_note(&k.added)).chain(k.told).collect()
}

/// One line for the LaTeX in what a write added: which fields hold it, as written, and what to write instead.
pub fn note(added: &[(&'static str, String)]) -> Option<String> {
    let mut fields: Vec<(&str, Vec<Mark>)> = Vec::new();
    for (field, text) in added {
        let found = marks(text);
        if found.is_empty() {
            continue;
        }
        match fields.iter_mut().find(|(f, _)| f == field) {
            Some((_, ms)) => found.into_iter().for_each(|m| {
                if !ms.contains(&m) {
                    ms.push(m)
                }
            }),
            None => fields.push((field, found)),
        }
    }
    if fields.is_empty() {
        return None;
    }
    let said: Vec<String> = fields
        .iter()
        .map(|(f, ms)| format!("{f} ({})", crate::assess::first_few(&ms.iter().map(|m| m.shown.clone()).collect::<Vec<_>>(), 3, ", ")))
        .collect();
    let has = |k: Kind| fields.iter().any(|(_, ms)| ms.iter().any(|m| m.kind == k));
    let advice: Vec<String> = [
        (Kind::Math, "Unicode symbols (≥, ×, Δ, σ, ∼) for math"),
        (Kind::Format, "the words without their command"),
        (Kind::Macro, "the name each macro stands for"),
        (Kind::Cite, "no citation keys"),
        (Kind::Ref, "no table or figure references the record cannot follow"),
    ]
    .iter()
    .filter(|(k, _)| has(*k))
    .map(|(_, a)| a.to_string())
    .collect();
    Some(format!(
        "LaTeX markup in {}. rb shows text exactly as written, on every screen: write plain text, with {}.",
        and_list(&said),
        and_list(&advice)
    ))
}

/// The words in a text about who wrote it, or how: the writer's own ids (its runtime's name, its model's id), and
/// agent, session, memory and UNSUPPORTED where they speak of the record's own writing ("the recording agent", "from
/// session memory", "agent:codex"), each as written. Kept narrow, so a research name (ResNet-50, ViT-B/16, T5-11B), an
/// RL agent, a training session or GPU memory never counts.
pub fn writer_marks(text: &str, writer: &[String]) -> Vec<String> {
    static WORDS: std::sync::OnceLock<Vec<regex::Regex>> = std::sync::OnceLock::new();
    let words = WORDS.get_or_init(|| {
        [
            r"\bUNSUPPORTED\b",
            r"\[AGENT[^\]\n]{0,40}\]",
            r"(?i)\bagent:[A-Za-z0-9_.@+-]+",
            r"(?i)\b(?:recording|coding|writing|transcribing)\s+agent\b",
            r"(?i)\bagent(?:'s|s')?\s+(?:memory|recollection|summary|observations?|notes?|session)\b",
            r"(?i)\b(?:recorded|transcribed|written|noted|entered|added|logged|reconstructed|inferred|reported|typed|summari[sz]ed)\s+by\s+(?:the\s+|this\s+|an?\s+)?(?:[a-z]+\s+)?agent\b",
            r"(?i)\bagent-(?:reported|derived|written|authored|inferred|generated|recorded|sourced)\b",
            r"(?i)\b(?:this|current|previous|earlier|chat|coding|agent|recording)\s+session\b",
            r"(?i)\bsession(?:'s)?\s+(?:memory|notes?|summary|context|transcript|recollection)\b",
            r"(?i)\bsession-(?:derived|reported|sourced)\b",
            r"(?i)\bfrom\s+(?:my\s+|the\s+agent's\s+|the\s+session's\s+|session\s+)?memory\b",
            r"(?i)\bmy\s+memory\b",
            r"(?i)\bthe\s+agent\s+(?:wrote|inferred|recorded|recalled|added|entered|noted|guessed|assumed|reconstructed|typed|filled\s+in)\b",
            r"(?i)\b(?:numbers|values|results|figures|scores|seeds?)\s+(?:were\s+|are\s+)?recalled\b",
        ]
        .iter()
        .map(|p| regex::Regex::new(p).unwrap())
        .collect()
    });
    let mut found: Vec<(usize, String)> = Vec::new();
    for re in words {
        found.extend(re.find_iter(text).map(|m| (m.start(), m.as_str().to_string())));
    }
    let lower = text.to_lowercase();
    let part = |c: Option<char>| c.map_or(false, |c| c.is_alphanumeric() || "-_.".contains(c));
    // an id is a runtime's name or a model's, never a plain word a writer happened to be named (agent:another)
    let id_like = |w: &str| crate::actor::RUNTIME_NAMES.iter().any(|(k, _)| *k == w) || w.chars().any(|c| !c.is_alphabetic());
    for id in writer.iter().map(|w| w.trim().to_lowercase()).filter(|w| w.chars().count() >= 3 && id_like(w)) {
        for (at, _) in lower.match_indices(&id) {
            let before = lower[..at].chars().next_back();
            let mut rest = lower[at + id.len()..].chars();
            let (next, then) = (rest.next(), rest.next());
            // an id ends where the name does: gpt-5 is not gpt-5.1, and claude-code is not claude-codes
            let ends = !next.map_or(false, char::is_alphanumeric) && !(next.map_or(false, |c| "-_.".contains(c)) && then.map_or(false, char::is_alphanumeric));
            if !part(before) && ends {
                if let Some(shown) = text.get(at..at + id.len()) {
                    found.push((at, shown.to_string()));
                }
            }
        }
    }
    // a runtime's bare name (Claude, Codex) only where it says who wrote ("written by Claude", "Claude inferred"), as written
    for bare in writer.iter().filter_map(|w| bare_name(w)) {
        let name = regex::escape(&bare);
        let by = format!(r"(?i)\b(?:written|recorded|added|entered|noted|inferred|typed|reconstructed|transcribed|logged|reported|guessed)\s+by\s+({name})\b(?:[^-.\w]|\.(?:\s|$)|$)");
        let did = format!(r"(?i)\b({name})\s+(?:wrote|inferred|recorded|recalled|added|entered|noted|guessed|assumed|reconstructed|typed)\b");
        for re in [by, did].iter().filter_map(|p| regex::Regex::new(p).ok()) {
            found.extend(re.captures_iter(text).filter_map(|c| c.get(1)).map(|m| (m.start(), m.as_str().to_string())));
        }
    }
    // longest first at each place, and a mark inside one already kept is not said again
    found.sort_by(|a, b| a.0.cmp(&b.0).then(b.1.len().cmp(&a.1.len())));
    let mut spans: Vec<(usize, usize)> = Vec::new();
    let mut out: Vec<String> = Vec::new();
    for (at, m) in found {
        let end = at + m.len();
        if spans.iter().any(|(a, b)| *a <= at && end <= *b) {
            continue;
        }
        spans.push((at, end));
        if !out.iter().any(|o| o.eq_ignore_ascii_case(&m)) {
            out.push(m);
        }
    }
    out
}

/// A runtime's bare name from one of its writer's ids: claude from claude-code or Claude Code, codex from codex.
fn bare_name(id: &str) -> Option<String> {
    let low = id.trim().to_lowercase();
    crate::actor::RUNTIME_NAMES
        .iter()
        .find(|(k, label)| *k == low || label.to_lowercase() == low)
        .and_then(|(k, _)| k.split(['-', '_', ' ']).next())
        .filter(|b| b.chars().count() >= 4 && b.chars().all(char::is_alphabetic))
        .map(str::to_string)
}

/// One line for the words in what a write added about who wrote it, or how: which fields hold them, as written. rb
/// keeps the text; the record already keeps who made each write.
pub fn writer_note(added: &[(&'static str, String)], writer: &[String]) -> Option<String> {
    let mut fields: Vec<(&str, Vec<String>)> = Vec::new();
    for (field, text) in added {
        let found = writer_marks(text, writer);
        if found.is_empty() {
            continue;
        }
        match fields.iter_mut().find(|(f, _)| f == field) {
            Some((_, ms)) => found.into_iter().for_each(|m| {
                if !ms.contains(&m) {
                    ms.push(m)
                }
            }),
            None => fields.push((field, found)),
        }
    }
    if fields.is_empty() {
        return None;
    }
    let said: Vec<String> =
        fields.iter().map(|(f, ms)| format!("{f} ({})", crate::assess::first_few(&ms.iter().map(|m| format!("\"{m}\"")).collect::<Vec<_>>(), 3, ", "))).collect();
    Some(format!(
        "Words about who wrote it, or how, in {}. The record keeps who made each write: say what the text rests on instead, a file and line, or nothing.",
        and_list(&said)
    ))
}

/// Whether the quotation that spans `at..end` in `text` quotes a file: a file (and line), or a verb that says it is a
/// quote (reads, says, states), stands right before it, or a file and line right after it. Quotation marks around
/// anything else are the session's own words, and count.
fn quotes_a_file(text: &str, at: usize, end: usize) -> bool {
    static BEFORE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    static AFTER: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let before = BEFORE.get_or_init(|| {
        regex::Regex::new(r"(?i)(?:[\w./-]+\.[A-Za-z0-9]+(?::\d+(?:[-–]\d+)?)?|\b(?:reads?|says?|said|states?|stated|writes?|wrote|quotes?|quoted|quoting|(?:line|row)\s+\d+))\s*[:,]?\s*$").unwrap()
    });
    let after = AFTER.get_or_init(|| regex::Regex::new(r"^\s*[(\[,]?\s*(?:at\s+|in\s+)?[\w./-]+\.[A-Za-z0-9]+:\d+").unwrap());
    before.is_match(&text[..at]) || after.is_match(&text[end..])
}

/// The text with what it quotes of a file blanked out, at the same byte offsets: double quotes, curly quotes, and
/// single quotes that open at the start of a word and close where no letter follows (an apostrophe inside a word, the
/// researcher's, is neither), where a file stands beside them (quotes_a_file).
fn unquoted(text: &str) -> String {
    let chars: Vec<(usize, char)> = text.char_indices().collect();
    let mut blank: Vec<(usize, usize)> = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        let (at, c) = chars[i];
        let before = i.checked_sub(1).map(|j| chars[j].1);
        let close = match c {
            '"' => Some('"'),
            '\u{201c}' => Some('\u{201d}'),
            '\u{2018}' if before.map_or(true, |b| !b.is_alphanumeric()) => Some('\u{2019}'),
            '\'' if before.map_or(true, |b| !b.is_alphanumeric()) => Some('\''),
            _ => None,
        };
        if let Some(close) = close {
            let single = close == '\'' || close == '\u{2019}';
            let end = (i + 1..chars.len()).find(|&j| chars[j].1 == close && (!single || chars.get(j + 1).map_or(true, |n| !n.1.is_alphanumeric())));
            if let Some(j) = end {
                let end = chars[j].0 + chars[j].1.len_utf8();
                if quotes_a_file(text, at, end) {
                    blank.push((at, end));
                }
                i = j + 1;
                continue;
            }
        }
        i += 1;
    }
    let mut out = text.to_string();
    for (a, b) in blank {
        out.replace_range(a..b, &" ".repeat(b - a));
    }
    out
}

/// The words in a session's text that give the user's say, or the chat, as what it rests on ("the researcher's rule",
/// "left to the researcher", "stated in this chat", "the account given in the session"), each as written. A file's
/// words in quotation marks, with the file beside them, never count; nor do research words that only look like these
/// (a chat model, a user study, one training session).
pub fn attribution_marks(text: &str) -> Vec<String> {
    static WORDS: std::sync::OnceLock<Vec<regex::Regex>> = std::sync::OnceLock::new();
    const SAID: &str = "said|says|stated|states|decided|decides|wants|wanted|asked|asks|told|recalled|recalls|remembers|remembered|confirmed|chose|chosen|prefers|preferred|intends|intended|expects|expected|believes|believed|thinks|thought|ruled|mentioned|accepted|rejected|agreed|planned|gave|described|explained|specified|requested|instructed|set|noted|notes|indicated|indicates|picked|suggested|suggests|proposed|flagged|put\\s+it";
    const THEIRS: &str = "rule|rules|words|call|calls|decision|decisions|threshold|thresholds|plan|plans|recollection|memory|instruction|instructions|account|next\\s+step|choice|bar|criterion|gate|view|claim";
    const NOT_A_SOURCE: &[&str] = &["model", "models", "template", "templates", "format", "formats", "data", "dataset", "datasets", "setting", "settings", "version", "versions", "variant", "variants", "mode", "task", "tasks", "benchmark", "benchmarks", "turn", "turns", "history", "logs", "length", "lengths", "transcripts", "corpus", "app", "apps", "interface", "interfaces", "window", "client", "clients", "bot", "bots"];
    let words = WORDS.get_or_init(|| {
        [
            r"(?i)\b(?:the\s+|our\s+)?(?:researcher|PI)(?:'s|’s)(?:\s+own)?\s+[A-Za-z]+(?:\s+step)?".to_string(),
            format!(r"(?i)\b(?:the\s+|our\s+)?user(?:'s|’s)(?:\s+own)?\s+(?:{THEIRS})\b"),
            format!(r"(?i)\b(?:the|our)\s+(?:researcher|user|PI|human)\s+(?:has\s+|had\s+)?(?:{SAID})\b"),
            r"(?i)\b(?:from|with)\s+the\s+(?:researcher|PI)\b".to_string(),
            r"(?i)\bthe\s+(?:researcher|user|PI)\s+and\s+I\b".to_string(),
            r"(?i)\bwe\s+(?:have\s+|had\s+)?agreed\b".to_string(),
            r"(?i)\b(?:left|up)\s+to\s+the\s+(?:researcher|user)\b".to_string(),
            r"(?i)\bfor\s+the\s+(?:researcher|user)\s+to\s+(?:decide|set|choose|confirm|say|state)\b".to_string(),
            r"(?i)\b(?:per|according\s+to|(?:as\s+)?(?:told|stated|said|given|recalled|confirmed|decided|chosen|specified|noted|set|suggested|proposed)\s+by)\s+the\s+(?:researcher|user|PI|human)\b".to_string(),
            format!(r"(?i)\byou\s+(?:have\s+|had\s+)?(?:{SAID})\b"),
            format!(r"(?i)\byour\s+(?:own\s+)?(?:{THEIRS})\b"),
            r"(?i)\b(?:given|stated|said|told|recalled|described|mentioned|reported|discussed|provided|explained|confirmed|agreed|decided)\s+(?:earlier\s+)?(?:in|during|over)\s+(?:the|this|our)\s+(?:session|chat|conversation)\b".to_string(),
            r"(?i)\b(?:the|this)\s+session(?:'s|’s)\s+(?:account|words|claim|view)\b".to_string(),
            r"(?i)\b(?:according\s+to|per)\s+the\s+session\b".to_string(),
            // from the session as a source, never logs or a checkpoint from it
            r"(?i)\bfrom\s+the\s+session(?:(?:'s|’s)?\s+(?:account|words|memory|context|notes)\b|\s*(?:[.,;:)]|$))".to_string(),
            r"(?i)\b(?:stated|said|given|agreed|discussed|mentioned|told|decided)\s+in\s+conversation\b".to_string(),
        ]
        .iter()
        .map(|p| regex::Regex::new(p).unwrap())
        .collect()
    });
    static CHAT: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let chat = CHAT.get_or_init(|| {
        regex::Regex::new(r"(?i)\b(?:(?:in|from|during|per|through|over)\s+(?:this|the|our)|this|our)\s+(?:chat|conversation)\b(?:[\s-]+([A-Za-z]+))?").unwrap()
    });
    let text = unquoted(text);
    let mut found: Vec<(usize, String)> = Vec::new();
    for re in words {
        found.extend(re.find_iter(&text).map(|m| (m.start(), m.as_str().to_string())));
    }
    for c in chat.captures_iter(&text) {
        if c.get(1).map_or(false, |n| NOT_A_SOURCE.contains(&n.as_str().to_lowercase().as_str())) {
            continue;
        }
        let m = c.get(0).unwrap();
        let end = c.get(1).map_or(m.end(), |n| m.start() + text[m.start()..n.start()].trim_end_matches(|ch: char| ch.is_whitespace() || ch == '-').len());
        found.push((m.start(), text[m.start()..end].to_string()));
    }
    found.sort_by(|a, b| a.0.cmp(&b.0).then(b.1.len().cmp(&a.1.len())));
    let mut spans: Vec<(usize, usize)> = Vec::new();
    let mut out: Vec<String> = Vec::new();
    for (at, m) in found {
        let end = at + m.len();
        if spans.iter().any(|(a, b)| at < *b && *a < end) {
            continue;
        }
        spans.push((at, end));
        if !out.iter().any(|o| o.eq_ignore_ascii_case(&m)) {
            out.push(m);
        }
    }
    out
}

/// One line for the words in what a write added that give the user's say, or the chat, as what it rests on: which
/// fields hold them, as written. rb keeps the text.
pub fn attribution_note(added: &[(&'static str, String)]) -> Option<String> {
    let mut fields: Vec<(&str, Vec<String>)> = Vec::new();
    for (field, text) in added {
        let found = attribution_marks(text);
        if found.is_empty() {
            continue;
        }
        match fields.iter_mut().find(|(f, _)| f == field) {
            Some((_, ms)) => found.into_iter().for_each(|m| {
                if !ms.contains(&m) {
                    ms.push(m)
                }
            }),
            None => fields.push((field, found)),
        }
    }
    if fields.is_empty() {
        return None;
    }
    let said: Vec<String> =
        fields.iter().map(|(f, ms)| format!("{f} ({})", crate::assess::first_few(&ms.iter().map(|m| format!("\"{m}\"")).collect::<Vec<_>>(), 3, ", "))).collect();
    Some(format!(
        "Words that name the user or the chat as a text's source, in {}. A text says what it rests on, a file and line, or nothing; words recalled from the user go in --words, which rb shows as words (as given).",
        and_list(&said)
    ))
}

fn and_list(items: &[String]) -> String {
    match items {
        [] => String::new(),
        [one] => one.clone(),
        [rest @ .., last] => format!("{} and {last}", rest.join(", ")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shown(text: &str) -> Vec<String> {
        marks(text).into_iter().map(|m| m.shown).collect()
    }

    /// What a paper's LaTeX leaves in a sentence is found as written.
    #[test]
    fn latex_a_paper_writes_is_found() {
        assert_eq!(shown(r"INT8 adds \emph{at most} 0.05"), [r"\emph{at most}"]);
        assert_eq!(shown(r"the \texttt{lr} sweep"), [r"\texttt{lr}"]);
        assert_eq!(shown(r"as in \cite{smith2020}"), [r"\cite{smith2020}"]);
        assert_eq!(shown(r"as in~\citep[p.~3]{smith2020}"), [r"\citep[p.~3]{smith2020}"]);
        assert_eq!(shown(r"see Table~\ref{tab:main}"), [r"\ref{tab:main}"]);
        assert_eq!(shown(r"a \Delta of 0.3"), [r"\Delta"]);
        assert_eq!(shown(r"loss falls by $\Delta$ 0.3"), [r"$\Delta$"]);
        assert_eq!(shown(r"grows as $x^2$ and $\sigma_i$"), [r"$x^2$", r"$\sigma_i$"]);
        assert_eq!(shown(r"$$a_{ij}$$ holds"), [r"$$a_{ij}$$"]);
        assert_eq!(shown(r"the R$^2$ rises"), [r"$^2$"]);
        assert_eq!(shown(r"gains $-2.02$ ($-9.9\%$)"), [r"\%"]);
        assert_eq!(shown(r"costs ${\cal O}(n)$ and $(a+b)^2$"), [r"${\cal O}(n)$", r"$(a+b)^2$"]);
        assert_eq!(shown(r"the \ourmodel{} model"), [r"\ourmodel{}"]);
        assert_eq!(marks(r"the \ourmodel{} model")[0].kind, Kind::Macro);
        assert_eq!(marks(r"\cite{a}")[0].kind, Kind::Cite);
        assert_eq!(marks(r"\ref{a}")[0].kind, Kind::Ref);
        assert_eq!(marks(r"\emph{a}")[0].kind, Kind::Format);
        assert_eq!(shown(r"\emph{a much longer phrase than any mark shows}"), [r"\emph{…}"]);
    }

    /// Nothing else is taken for LaTeX: prices, shell and Make variables, regexes, paths, addresses and code spans.
    #[test]
    fn what_only_looks_like_latex_is_not() {
        for text in [
            "it costs $5 and $10 a run",
            "$5-$10 per GPU hour",
            "between $5_000 and 10$",
            "set $RB_MODEL/$RB_SESSION first",
            "checkpoints are in ${OUTPUT_DIR}/${RUN_NAME}",
            "${DATA_ROOT}/train/${SPLIT}",
            "${MODEL_NAME}-${SEED}.pt",
            "$(OUT_DIR)/$(RUN)",
            "$DATA_ROOT/${SPLIT}",
            "the rule writes $(BUILD_DIR)/$@",
            "under ${OUT_DIR} for about 5$",
            r"the regex ^run_\d+$ matches",
            r"(^\d+$|^\w+$) and \bword\b",
            r"\s*\beta",
            r"C:\Users\ana\delta\times.csv",
            r"out\delta\run1\metrics.json",
            r"\\server\share\to",
            r"%APPDATA%\alpha",
            "https://example.org/$x^2$/page",
            r"see `$\Delta$ and \emph{x}` in the code",
            r"split on \n and \t",
            r"match \w{2,} or \bx{3}",
            r"a $n=3$ run",
            "an em dash --- here",
        ] {
            assert!(marks(text).is_empty(), "{text}: {:?}", marks(text));
        }
    }

    /// The line names each field with what it holds, and says what to write instead for what it found.
    #[test]
    fn the_line_names_the_field_and_what_to_write() {
        let line = note(&[("statement", r"gains $\Delta$ \emph{fast}".into()), ("when_met", r"as \cite{x} says".into())]).unwrap();
        assert_eq!(
            line,
            r"LaTeX markup in statement ($\Delta$, \emph{fast}) and when_met (\cite{x}). rb shows text exactly as written, on every screen: write plain text, with Unicode symbols (≥, ×, Δ, σ, ∼) for math, the words without their command and no citation keys."
        );
        assert_eq!(note(&[("text", "plain words, 5 dollars".into())]), None);
    }

    /// The words about the record's own writing are found, and research words that only look like them are not.
    #[test]
    fn words_about_the_writing_are_found() {
        let ids = ["claude-code".to_string(), "Claude Code".to_string(), "example-model-7".to_string()];
        let found = |t: &str| writer_marks(t, &ids);
        assert_eq!(found("Transcribed by the recording agent from the paper"), ["Transcribed by the recording agent"]);
        assert_eq!(found("UNSUPPORTED (session memory): it stopped early"), ["UNSUPPORTED", "session memory"]);
        assert_eq!(found("[AGENT OBSERVATION] the loss fell"), ["[AGENT OBSERVATION]"]);
        assert_eq!(found("noted by agent:codex, originally run in Claude Code"), ["noted by agent", "agent:codex", "Claude Code"]);
        assert_eq!(found("this session recalled it from memory"), ["this session", "from memory"]);
        assert_eq!(found("written by example-model-7"), ["example-model-7"]);
        for text in [
            "ResNet-50, ViT-B/16 and T5-11B on ImageNet",
            "the PPO agent learns faster than the DQN agent",
            "an agent-based model of traffic",
            "GPU memory peaks at 12 GB; the batch fits in memory",
            "one training session per seed",
            "example-model-7b is a different model, as is example-model-70",
            "UNSUPPORTEDLY is not a word",
        ] {
            assert!(found(text).is_empty(), "{text}: {:?}", found(text));
        }
        // a writer named by a plain word is not found in every sentence that uses it
        assert!(writer_marks("written through another State", &["another".to_string()]).is_empty());
        assert_eq!(writer_marks("codex wrote it", &["codex".to_string()]), ["codex"]);
        // the agent's own writing in plain words, and the runtime's bare name where it says who wrote
        assert_eq!(found("The agent inferred the seed"), ["The agent inferred"]);
        assert_eq!(found("Numbers recalled, not read"), ["Numbers recalled"]);
        assert_eq!(found("Written by Claude, UNSUPPORTED claim"), ["Claude", "UNSUPPORTED"]);
        assert_eq!(found("written by Claude."), ["Claude"]);
        assert_eq!(found("Claude inferred the seed"), ["Claude"]);
        assert_eq!(writer_marks("Codex added these runs", &["codex".to_string()]), ["Codex"]);
        for text in ["Claude-3 and Claude 3.5 inferred labels on ImageNet", "the agent learned to balance", "recall rose to 0.8", "written by Claude-3"] {
            assert!(found(text).is_empty(), "{text}: {:?}", found(text));
        }
        assert!(writer_marks("Written by Claude", &["example-model-7".to_string()]).is_empty());
    }

    /// A session's text that gives the user, the chat or the session as its source is found, as written; quoted words
    /// and research words that only look like it are not.
    #[test]
    fn words_that_name_the_user_or_the_chat_as_a_source_are_found() {
        let found = |t: &str| attribution_marks(t);
        assert_eq!(
            found("The researcher's rule is that int8 must keep top-1 within a point of fp32; that threshold is left to the researcher."),
            ["The researcher's rule", "left to the researcher"]
        );
        assert_eq!(found("the researcher's rule for the next step, stated in this chat"), ["the researcher's rule", "stated in this chat"]);
        assert_eq!(found("the next step stated in this chat: fill the arm to three seeds"), ["stated in this chat"]);
        assert_eq!(found("the account given in the session is that it was stopped by hand"), ["given in the session"]);
        assert_eq!(found("As the user said, top-1 must stay within 0.5"), ["the user said"]);
        assert_eq!(found("per the researcher, seeds 1 to 3"), ["per the researcher"]);
        assert_eq!(found("Your rule is the experiment's gate"), ["Your rule"]);
        assert_eq!(found("as you said earlier, INT8 ships if it holds"), ["you said"]);
        assert_eq!(found("recalled from the chat: it was stopped"), ["from the chat"]);
        assert_eq!(found("the user’s threshold"), ["the user’s threshold"]);
        // close variants a hurried session writes
        for (text, mark) in [
            ("the researcher set the threshold at 0.01", "the researcher set"),
            ("the researcher noted that seed 2 failed", "the researcher noted"),
            ("as noted by the researcher, seed 2 failed", "as noted by the researcher"),
            ("a threshold from the researcher: 0.01", "from the researcher"),
            ("as discussed with the researcher", "with the researcher"),
            ("the researcher and I agreed on 3 seeds", "the researcher and I"),
            ("we agreed on 3 seeds", "we agreed"),
            ("the researcher indicated 3 seeds", "the researcher indicated"),
            ("the user picked 3 seeds", "the user picked"),
            ("the PI decided 3 seeds", "the PI decided"),
            ("The PI's rule: 3 seeds", "The PI's rule"),
            ("stopped by hand on May 7, as the researcher put it", "the researcher put it"),
            ("stopped by hand on May 7 according to the human", "according to the human"),
            ("stopped by hand on May 7; source: this conversation", "this conversation"),
            ("stopped by hand on May 7, as stated in conversation", "stated in conversation"),
            ("recalled from the session.", "from the session."),
            ("'the researcher's rule' is 3 seeds", "the researcher's rule"),
            ("\"The researcher's rule\": 3 seeds", "The researcher's rule"),
        ] {
            assert_eq!(found(text), [mark], "{text}");
        }
        for text in [
            "logs from the session at 3 am on the cluster",
            "4be1a7d2 resumed from the session checkpoint at step 12,400 (train/main.py:88)",
            "users in this chat app see a lag",
            "feedback from the user and clicks from the user",
            "the model's behaviour in conversation with a tutor",
            "\"left to the researcher\" (paper.tex:40)",
            "the user sets a budget in the survey's second task",
            "README.md:12 says 'the researcher's choice of seed is arbitrary'",
            "paper.tex:40 reads \"left to the researcher\"",
            "the paper says “as the user said” in its survey section",
            "researchers have shown INT8 holds on ImageNet",
            "a user study with 12 users; the user interface",
            "the chat model and the chat template; in the chat data",
            "one training session per seed; the session length is 40 steps",
            "no file I read records this: docs/plan.md, results.csv",
            "INT8 ResNet-50 loses 0.3 top-1 on ImageNet",
            "the user's query and the user's click",
        ] {
            assert!(found(text).is_empty(), "{text}: {:?}", found(text));
        }
        let line = attribution_note(&[("why", "the researcher's rule, stated in this chat".into())]).unwrap();
        assert_eq!(
            line,
            "Words that name the user or the chat as a text's source, in why (\"the researcher's rule\", \"stated in this chat\"). A text says what it rests on, a file and line, or nothing; words recalled from the user go in --words, which rb shows as words (as given)."
        );
        assert_eq!(attribution_note(&[("text", "results.csv row 12 gives top1 0.761".into())]), None);
    }

    #[test]
    fn the_writer_line_names_the_field_and_what_to_write() {
        let line = writer_note(&[("text", "UNSUPPORTED: the recording agent saw it".into())], &[]).unwrap();
        assert_eq!(
            line,
            "Words about who wrote it, or how, in text (\"UNSUPPORTED\", \"recording agent\"). The record keeps who made each write: say what the text rests on instead, a file and line, or nothing."
        );
        assert_eq!(writer_note(&[("text", "the RL agent".into())], &[]), None);
    }

    /// A write adds what its record did not hold before; text it held already is not said again.
    #[test]
    fn only_what_a_write_adds_is_kept() {
        set_aside();
        let before = vec![("why", r"$\Delta$ again".to_string())];
        let after = vec![("why", r"$\Delta$ again".to_string()), ("note", r"\emph{new}".to_string())];
        added(false, "add", &[], &after, &[]);
        assert!(take_note().is_empty());
        added(true, "clone", &[], &after, &[]);
        assert!(take_note().is_empty());
        added(true, "add", &before, &after, &[]);
        assert_eq!(set_aside().added, vec![("note", r"\emph{new}".to_string())]);
    }
}
