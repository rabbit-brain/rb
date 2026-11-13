//! Where a reader can get a resource, computed on read from what the record already holds, never fetched: a Hugging
//! Face id is its page on huggingface.co, a URL stays a URL, a file the repository's HEAD holds is its path there, and
//! otherwise there is no download link. A citation or a licence recorded as where it comes from is a chip beside it,
//! never the way to get it. A local copy, and whether it is on this machine, is the owner's alone: a page anyone may
//! be sent names no local path. And where rb can see a resource's origin in a path it is given (the Hugging Face
//! cache, a DVC pointer beside it, a config that names it with an address), it records that origin itself.

use crate::ledger::Ledger;
use crate::record::{Location, Resource};
use crate::value::{Dict, Value};
use std::path::Path;

/// The types a reader downloads, for which rb asks for a fetchable source when none is recorded.
pub const ASKED: &[&str] = &["dataset", "checkpoint"];

/// A Hugging Face id as a location records it (`org/name`, `datasets/org/name`, with `@<revision>` after it or not):
/// its page, `datasets/` before it for a dataset. None for a ref that is no id.
pub fn hf_href(r#ref: &str, r#type: &str) -> Option<String> {
    let r = r#ref.trim().trim_start_matches("https://huggingface.co/").trim_matches('/');
    let (id, rev) = match r.split_once('@') {
        Some((i, v)) => (i, Some(v)),
        None => (r, None),
    };
    let parts: Vec<&str> = id.split('/').collect();
    let named = |p: &&str| !p.is_empty() && p.chars().all(|c| c.is_ascii_alphanumeric() || "-_.".contains(c));
    let path = match parts.as_slice() {
        [kind, _, _] if (*kind == "datasets" || *kind == "spaces") && parts[1..].iter().all(named) => id.to_string(),
        [kind, _, _] if *kind == "models" && parts[1..].iter().all(named) => parts[1..].join("/"),
        [_, _] if parts.iter().all(named) => {
            if r#type == "dataset" {
                format!("datasets/{id}")
            } else {
                id.to_string()
            }
        }
        [_] if parts.iter().all(named) && r#type != "dataset" => id.to_string(),
        _ => return None,
    };
    let tree = rev.filter(|v| !v.is_empty() && v.chars().all(|c| c.is_ascii_alphanumeric() || "-_.".contains(c))).map(|v| format!("/tree/{v}")).unwrap_or_default();
    Some(format!("https://huggingface.co/{path}{tree}"))
}

fn is_url(s: &str) -> bool {
    let s = s.trim();
    (s.starts_with("https://") || s.starts_with("http://")) && s.len() > 8 && !s.contains(char::is_whitespace)
}

/// An address a page may give anyone: no credentials before its host and no query or fragment, where a signed link
/// keeps its token. Any other is the owner's alone, as a download address is (tier: DownloadAddress).
pub fn is_public_url(s: &str) -> bool {
    let s = s.trim();
    let rest = s.split_once("://").map_or("", |(_, r)| r);
    let host = rest.split('/').next().unwrap_or("");
    is_url(s) && !host.contains('@') && !s.contains(['?', '#'])
}

/// Whether a local location's ref names a path outside the repository: absolute in either system's form, a UNC path
/// (which names a host), or one that climbs out. Such a path is another machine's, or this one's outside the work.
pub fn outside(r#ref: &str) -> bool {
    let r = r#ref.trim();
    let r = r.strip_prefix("file://").or_else(|| r.strip_prefix("file:")).unwrap_or(r);
    Path::new(r).is_absolute() || r.starts_with('/') || r.starts_with('\\') || r.starts_with('~') || r.get(1..3).map_or(false, |x| x == ":/" || x == ":\\") || r.starts_with("../") || r == ".."
}

/// Whether a place recorded for a resource is the owner's alone: a path outside the repository, given under any
/// provider (`local:/home/<name>/...`, `other:/Users/<name>/...`), or an address with a credential or a token in it.
pub fn private_place(provider: &str, r#ref: &str) -> bool {
    let r = r#ref.trim();
    if is_url(r) {
        return !is_public_url(r);
    }
    (provider == "local" || provider == "other" || provider == "url") && outside(r)
}

/// A resource record's fields with every private place (private_place) left out, and a recorded `url` that is one; and
/// in the words typed for it (`subset`, `acquisition.source`, `acquisition.recipe`), each local path outside the
/// repository said as LOCAL_PATH (without_local_paths). None where it holds none.
pub fn without_private_places(data: &Dict) -> Option<Dict> {
    let locs = data.get("locations").and_then(Value::as_list).cloned().unwrap_or_default();
    let private = |l: &Value| private_place(l.get_str("provider").unwrap_or(""), l.get_str("ref").unwrap_or(""));
    let url_private = data.get("acquisition").and_then(|a| a.get_str("url")).map_or(false, |u| !u.trim().is_empty() && !is_public_url(u));
    let typed = |d: &Dict, k: &str| d.get(k).and_then(Value::as_str).map_or(false, |s| !local_paths_in(s).is_empty());
    let acq_typed = data.get("acquisition").and_then(Value::as_dict).map_or(false, |a| TYPED_ACQUISITION.iter().any(|k| typed(a, k)));
    if !locs.iter().any(private) && !url_private && !typed(data, "subset") && !acq_typed {
        return None;
    }
    let mut out = data.clone();
    if locs.iter().any(private) {
        out.insert("locations".into(), Value::List(locs.into_iter().filter(|l| !private(l)).collect()));
    }
    if let Some(Value::Str(s)) = out.get_mut("subset") {
        *s = without_local_paths(s);
    }
    if let Some(Value::Dict(a)) = out.get_mut("acquisition") {
        if url_private {
            a.shift_remove("url");
        }
        for k in TYPED_ACQUISITION {
            if let Some(Value::Str(s)) = a.get_mut(*k) {
                *s = without_local_paths(s);
            }
        }
    }
    Some(out)
}

/// The words typed for how a resource is got again, which may name a path on the machine that typed them.
const TYPED_ACQUISITION: &[&str] = &["source", "recipe"];

/// What a page anyone may be sent, and a bundle, says in place of a local path outside the repository that someone
/// typed into a resource's words (a recipe's `--out /home/ana/data`, a source's `copied from /srv/lab/share`).
pub const LOCAL_PATH: &str = "<local path>";

/// The byte spans of `text` that are local paths outside the repository, as `outside` reads a location's ref: a path in
/// quotes whole, spaces and all (`"/opt/lab data/val"`); else each whitespace-separated word, or its part after `=` or
/// `:` (`--out=/home/ana/data`, `--out:/home/ana/data`) or after a short flag's letters (`-o/home/ana/data`), that is
/// an absolute path in either system's form, a UNC path, a home-relative path (`~/data`, `~ana/data`, `$HOME/data`,
/// `%USERPROFILE%\data`) or a relative one that climbs out (`../data`, `x/../../data`), its quotes and brackets and a
/// sentence's last mark left out. An address (`https://...`) is none, a `file:` address is one, and a word that only
/// holds a `/` (`GB/s`, `and/or`, `1/2`) is none. A path with a space and no quotes around it is said up to the space.
pub fn local_paths_in(text: &str) -> Vec<(usize, usize)> {
    let mut out: Vec<(usize, usize)> = Vec::new();
    // quoted paths first, whole
    let b = text.as_bytes();
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'"' || b[i] == b'\'' {
            if let Some(len) = text[i + 1..].find(b[i] as char) {
                let inner = &text[i + 1..i + 1 + len];
                if !inner.is_empty() && !inner.contains('\n') && inner.trim() == inner && looks_local(inner) {
                    out.push((i + 1, i + 1 + len));
                    i += len + 2;
                    continue;
                }
            }
        }
        i += 1;
    }
    // the rest, word by word, with each quoted path blanked out, so a word holds what is left beside it
    let mut masked = text.as_bytes().to_vec();
    for &(s, e) in &out {
        masked[s..e].iter_mut().for_each(|c| *c = b' ');
    }
    let masked = String::from_utf8(masked).unwrap_or_default();
    let mut at = 0;
    for word in masked.split_inclusive(char::is_whitespace) {
        let start = at;
        at += word.len();
        let w = word.trim_end_matches(char::is_whitespace);
        let lead = w.len() - w.trim_start_matches(['(', '"', '\'', '`', '[', '<', '{']).len();
        let body = w[lead..].trim_end_matches([')', '"', '\'', '`', ']', '>', '}', ',', ';', '.', ':', '!', '?']);
        if body.is_empty() {
            continue;
        }
        // where in the word a path may start: its start; after `=`, `:`, `>`, `,`, `(`, `[` or `@` (`--out=/...`,
        // `2>/...`, `a,/...`, `[copy](/...)`), never inside an address; after a short flag's letters (`-o/...`)
        let mut starts = vec![0];
        if !body.contains("://") {
            starts.extend(body.match_indices(['=', ':', '>', ',', '(', '[', '@']).map(|(k, _)| k + 1).filter(|&k| k < body.len()));
        }
        if let Some(flag) = body.strip_prefix('-') {
            let letters = flag.trim_start_matches('-').len();
            let name = flag.trim_start_matches('-');
            let n = name.find(|c: char| !c.is_ascii_alphabetic()).unwrap_or(name.len());
            if n > 0 && n < name.len() {
                starts.push(body.len() - letters + n);
            }
        }
        if let Some(k) = starts.into_iter().find(|&k| looks_local(&body[k..])) {
            // a path's own closing marks stay outside it (`[copy](/home/ana/x)`)
            let path = body[k..].trim_end_matches([')', ']', '"', '\'']);
            let s = start + lead + k;
            out.push((s, s + path.len()));
        }
    }
    out.sort();
    out
}

fn looks_local(w: &str) -> bool {
    let w = w.strip_prefix("file://").or_else(|| w.strip_prefix("file:")).unwrap_or(w);
    if w.contains("://") {
        return false;
    }
    let b = w.as_bytes();
    let named = |c: u8| c.is_ascii_alphanumeric() || b"._-~$%".contains(&c) || c >= 0x80;
    // `/data`, `/srv/x`: a first part of two characters at least, or one that goes on (`/x/y`), never a unit's `/s`
    let rooted = |rest: &str| {
        let first = rest.split(['/', '\\']).next().unwrap_or("");
        first.chars().count() >= 2 || (rest.len() > first.len() + 1 && !first.is_empty())
    };
    let sep = |c: char| c == '/' || c == '\\';
    // a home folder named by a variable: `$HOME/...`, `${HOME}/...`, `$env:USERPROFILE\...`, `%USERPROFILE%\...`
    // a variable as the environment names one (`HOME`, `DATA_DIR`, `${HOME}`, `$env:USERPROFILE`), never `$5` or `$k`
    let var_home = |rest: &str| {
        rest.find(sep).map_or(false, |k| {
            let name = rest[..k].trim_matches(['{', '}', '"']);
            let name = name.strip_prefix("env:").unwrap_or(name);
            name.len() >= 2 && name.starts_with(|c: char| c.is_ascii_uppercase() || c == '_') && name.chars().all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == '_')
        })
    };
    match b {
        [b'/', b'/', c, ..] | [b'\\', b'\\', c, ..] => named(*c),
        [b'/', c, ..] => named(*c) && rooted(&w[1..]),
        [b'~', b'/', ..] | [b'~', b'\\', ..] => true,
        [b'~', c, ..] if c.is_ascii_alphabetic() => w[1..].find(sep).map_or(false, |k| w[1..1 + k].chars().all(|c| c.is_alphanumeric() || "._-".contains(c))),
        [b'$', ..] => var_home(&w[1..]),
        [b'%', ..] => w[1..].find('%').map_or(false, |k| k > 0 && w[1 + k + 1..].starts_with(sep)),
        [d, b':', b'/' | b'\\', ..] => d.is_ascii_alphabetic(),
        _ => climbs_out(w),
    }
}

/// Whether a relative path climbs out of where it starts: `../data`, `x/../../data`.
fn climbs_out(w: &str) -> bool {
    if !w.contains("..") {
        return false;
    }
    let mut depth = 0i32;
    for part in w.split(['/', '\\']) {
        match part {
            ".." => {
                depth -= 1;
                if depth < 0 {
                    return true;
                }
            }
            "" | "." => {}
            _ => depth += 1,
        }
    }
    false
}

/// `text` with each local path outside the repository (local_paths_in) said as LOCAL_PATH.
pub fn without_local_paths(text: &str) -> String {
    let spans = local_paths_in(text);
    if spans.is_empty() {
        return text.to_string();
    }
    let mut out = String::with_capacity(text.len());
    let mut at = 0;
    for (s, e) in spans {
        out.push_str(&text[at..s]);
        out.push_str(LOCAL_PATH);
        at = e;
    }
    out.push_str(&text[at..]);
    out
}

/// A resource's typed words (its `subset`, `source` or `recipe`) as the reader of what is being built reads them: a page
/// anyone may be sent (rb page, rb export, rb sync) says each local path outside the repository in them as LOCAL_PATH;
/// rb's text and the user's own screens keep them as typed.
pub fn typed(text: &str) -> String {
    if crate::view::reader() == crate::view::Reader::Page {
        without_local_paths(text)
    } else {
        text.to_string()
    }
}

thread_local! {
    /// While a bundle's page is made: the repository whose HEAD says which files a reader gets with the code, since
    /// the bundle's copy of the record has no git of its own.
    static REPOSITORY: std::cell::RefCell<Option<std::path::PathBuf>> = const { std::cell::RefCell::new(None) };
}

/// `build`, with the files a reader gets with the code read from `root`'s HEAD.
pub fn with_repository<T>(root: &Path, build: impl FnOnce() -> T) -> T {
    struct Restore(Option<std::path::PathBuf>);
    impl Drop for Restore {
        fn drop(&mut self) {
            REPOSITORY.with(|r| *r.borrow_mut() = self.0.take());
        }
    }
    let _restore = Restore(REPOSITORY.with(|r| r.borrow_mut().replace(root.to_path_buf())));
    build()
}

/// The one way a reader gets `res`, in order: a Hugging Face id, a URL (the recorded address, then a location that is
/// one), a file the repository's HEAD holds; else none. `{kind: hf|url|repo|none, href, text, public}`: `href` is "" for a
/// repository file, whose `text` is its path from the repository's root; `public` is false only for an address with a
/// credential or a token in it, and is there on every `get`.
pub fn get(led: &Ledger, res: &Resource) -> Dict {
    let made = |kind: &str, href: String, text: String| crate::dict! {"kind" => kind, "href" => href, "text" => text, "public" => true};
    // an address with a credential or a token in it is the owner's: on a page it reads as none (view::resource_rows)
    for l in res.locations.iter().filter(|l| l.provider == "hf") {
        if let Some(h) = hf_href(&l.r#ref, &res.r#type) {
            return made("hf", h.clone(), h.trim_start_matches("https://").to_string());
        }
    }
    let urls: Vec<String> = res.acquisition.as_ref().and_then(|a| a.url.clone()).into_iter()
        .chain(res.locations.iter().filter(|l| l.provider != "local").map(|l| l.r#ref.clone()))
        .map(|u| u.trim().to_string())
        .filter(|u| is_url(u))
        .collect();
    // a public address first; one with a token in it only where none is public
    if let Some(u) = urls.iter().find(|u| is_public_url(u)).or(urls.first()) {
        let mut d = made("url", u.clone(), u.clone());
        d.insert("public".into(), Value::Bool(is_public_url(u)));
        return d;
    }
    let root = REPOSITORY.with(|r| r.borrow().clone()).unwrap_or_else(|| led.root.clone());
    for l in res.locations.iter().filter(|l| l.provider == "local" && !outside(&l.r#ref)) {
        let rel = l.r#ref.trim().trim_start_matches("./").trim_end_matches('/');
        if !rel.is_empty() && rel != "." && crate::sources::git(&root, &["cat-file", "-e", &format!("HEAD:{rel}")]).is_some() {
            return made("repo", String::new(), rel.to_string());
        }
    }
    made("none", String::new(), String::new())
}

/// `get` as a page anyone may be sent gives it: an address with a credential or a token in it reads as none.
pub fn for_a_page(get: &Dict) -> Dict {
    if get.get("public").and_then(Value::as_bool) == Some(false) {
        crate::dict! {"kind" => "none", "href" => "", "text" => "", "public" => true}
    } else {
        get.clone()
    }
}

/// What the record says a resource comes from, as chips: its `source` split at `;` and `,`, a licence said by its name
/// alone ("MIT license" is "MIT"), and nothing that is the download address itself. `["Cobbe et al. 2021", "MIT"]`.
pub fn cite(res: &Resource, get: &Dict) -> Vec<String> {
    res.acquisition.as_ref().map_or_else(Vec::new, |a| chips(&typed(&a.source), get))
}

fn chips(source: &str, get: &Dict) -> Vec<String> {
    let href = get.get("href").and_then(Value::as_str).unwrap_or("");
    let mut out: Vec<String> = Vec::new();
    for part in source.split([';', ',']) {
        let mut p = part.trim().to_string();
        for tail in [" license", " licence", " License", " Licence", " LICENSE"] {
            if let Some(s) = p.strip_suffix(tail) {
                p = s.trim().to_string();
            }
        }
        if !p.is_empty() && p != href && !out.contains(&p) && !outside(&p) && !(is_url(&p) && !is_public_url(&p)) {
            out.push(p);
        }
    }
    out
}

/// A local copy as the owner's own screens say it: on this machine, missing, or other bytes; a path outside the
/// repository said as the recording machine's.
pub fn copy_said(r#ref: &str, state: &str) -> String {
    let far = if outside(r#ref) { " (a path on the machine that recorded it)" } else { "" };
    match state {
        "here" => format!("copy here: {}", r#ref),
        "different bytes" => format!("other bytes here: {}{far}", r#ref),
        _ if outside(r#ref) => format!("missing here: {}{far}", r#ref),
        _ => format!("missing from this checkout: {}", r#ref),
    }
}

/// Where a resource stood when a run read or made it (`inputs[].availability`), as rb says it now: the state word the
/// record keeps, in words that say it was the recording machine's; nothing where a copy was there, as a run's is.
pub fn then_said(state: &str) -> String {
    match state {
        "present" => String::new(),
        "changed" => "the copy there held other bytes".into(),
        "remote" | "reconstructible" => "no copy was on the machine that recorded it".into(),
        "lost" => "nothing recorded where to get it then".into(),
        other => format!("{other} then"),
    }
}

/// The words every surface says of where to get it: `get it: <address>`, `get it: <path> in the repository`, or `no
/// download link`, then its chips.
pub fn said(get: &Dict, cite: &[String]) -> String {
    let text = get.get("text").and_then(Value::as_str).unwrap_or("");
    let head = match get.get("kind").and_then(Value::as_str).unwrap_or("none") {
        "hf" | "url" => format!("get it: {}", get.get("href").and_then(Value::as_str).unwrap_or(text)),
        "repo" => format!("get it: {text} in the repository"),
        _ => "no download link".to_string(),
    };
    std::iter::once(head).chain(cite.iter().cloned()).collect::<Vec<_>>().join(" \u{b7} ")
}

/// Whether rb asks for a fetchable source for `res`: a dataset or checkpoint with no way to get it and no
/// recipe that makes it again.
pub fn asks(res: &Resource, get: &Dict) -> bool {
    let recipe = res.acquisition.as_ref().and_then(|a| a.recipe.as_ref()).map_or(false, |r| !r.trim().is_empty());
    ASKED.contains(&res.r#type.as_str()) && get.get("kind").and_then(Value::as_str) == Some("none") && !recipe && res.retracted.is_none()
}

/// The line rb says where it asks for one, and the command that records it.
pub fn ask_words(res: &Resource) -> (String, String) {
    let hf = if res.r#type == "dataset" { "<org>/<name>" } else { "<org>/<model>" };
    (
        format!("No download link is recorded for {}: record where it can be downloaded, its URL or Hugging Face id ({hf}), with rb.", res.id),
        format!("rb resource locate {} url <its URL>", res.id),
    )
}

// ---------------------------------------------------------------- the origin rb sees for itself

/// Where rb can see a local file's origin with no network: the Hugging Face cache's own folder name
/// (`<models|datasets>--<org>--<name>`, with `snapshots/<revision>` below it), a DVC pointer beside it (`<path>.dvc`)
/// that imports it from an address, or a config the repository tracks that names it beside an address or a Hugging
/// Face id. Each as a location to record, with rb's note on what it read.
pub fn origins(root: &Path, path: &Path, r#type: &str) -> Vec<(String, String, String)> {
    let mut out: Vec<(String, String, String)> = Vec::new();
    if let Some(hf) = from_hf_cache(path, r#type) {
        out.push(("hf".into(), hf, "read from its Hugging Face cache path".into()));
        return out;
    }
    if let Some((provider, r)) = from_dvc(path) {
        out.push((provider, r, "read from its DVC pointer".into()));
        return out;
    }
    if let Ok(rel) = path.strip_prefix(root) {
        let rel = rel.to_string_lossy().replace('\\', "/");
        if let Some((provider, r, file)) = from_configs(root, &rel, r#type) {
            out.push((provider, r, format!("read from {file}")));
        }
    }
    out
}

/// `<models|datasets>--<org>--<name>` in the path, with the revision under `snapshots/` when the path holds one.
pub fn from_hf_cache(path: &Path, r#type: &str) -> Option<String> {
    let parts: Vec<String> = path.to_string_lossy().replace('\\', "/").split('/').map(str::to_string).collect();
    for (i, seg) in parts.iter().enumerate() {
        let (kind, rest) = match seg.split_once("--") {
            Some((k, r)) if k == "models" || k == "datasets" => (k, r),
            _ => continue,
        };
        let Some((org, name)) = rest.split_once("--") else { continue };
        if org.is_empty() || name.is_empty() || name.contains("--") {
            continue;
        }
        let rev = (parts.get(i + 1).map(String::as_str) == Some("snapshots")).then(|| parts.get(i + 2).cloned()).flatten().filter(|r| r.len() >= 7 && r.chars().all(|c| c.is_ascii_hexdigit()));
        // the cache keeps each repository under a folder of its own (hub/, or HF_HUB_CACHE's): one above it
        if i == 0 {
            continue;
        }
        let id = match (kind, r#type == "dataset") {
            ("datasets", false) => format!("datasets/{org}/{name}"),
            ("models", true) => format!("models/{org}/{name}"),
            _ => format!("{org}/{name}"),
        };
        return Some(id + &rev.map(|r| format!("@{r}")).unwrap_or_default());
    }
    None
}

/// A DVC pointer beside the path (`<path>.dvc`) whose dependency is an address (dvc import-url) or a git repository
/// (dvc import): `url` with the address, or `git` with the repository's.
fn from_dvc(path: &Path) -> Option<(String, String)> {
    let mut p = path.as_os_str().to_owned();
    p.push(".dvc");
    let text = std::fs::read_to_string(Path::new(&p)).ok()?;
    let v = crate::pyyaml::load(&text).ok()?;
    for dep in v.get("deps").and_then(Value::as_list).cloned().unwrap_or_default() {
        if let Some(u) = dep.get("repo").and_then(|r| r.get("url")).and_then(Value::as_str).filter(|u| is_public_url(u)) {
            return Some(("git".into(), u.trim().to_string()));
        }
        if let Some(u) = dep.get("path").and_then(Value::as_str).filter(|u| is_public_url(u)) {
            return Some(("url".into(), u.trim().to_string()));
        }
    }
    None
}

/// Keys whose value names a Hugging Face id outright, and those that do beside a `revision`.
const HF_KEYS: &[&str] = &["repo_id", "hf", "hf_id", "hf_repo", "hub_id"];
const HF_WITH_REVISION: &[&str] = &["name", "id", "repo"];
const URL_KEYS: &[&str] = &["url", "uri", "download", "download_url", "source_url"];

/// A mapping in a config the repository tracks (YAML, JSON or TOML, at most 200 files of 256 KB) whose value is the
/// path (or a folder that holds it), beside an address or a Hugging Face id: (provider, ref, the config's path).
fn from_configs(root: &Path, rel: &str, r#type: &str) -> Option<(String, String, String)> {
    let listed = crate::sources::git(root, &["ls-files", "-z", "--", "*.yaml", "*.yml", "*.json", "*.toml"])?;
    for file in listed.split('\0').filter(|f| !f.is_empty() && !f.starts_with(".rb/") && !f.contains("/.rb/")).take(200) {
        let full = root.join(file);
        if std::fs::metadata(&full).map_or(true, |m| m.len() > 256 * 1024) {
            continue;
        }
        let Ok(v) = crate::sources::load_config(&full) else { continue };
        if let Some((p, r)) = beside(&v, rel, r#type) {
            return Some((p, r, file.to_string()));
        }
    }
    None
}

/// Whether a config's value names the file or folder itself (never a folder above it, which holds other things too).
fn names_path(v: &str, rel: &str) -> bool {
    let v = v.trim().trim_start_matches("./").trim_end_matches('/');
    !v.is_empty() && v == rel.trim_end_matches('/')
}

fn beside(v: &Value, rel: &str, r#type: &str) -> Option<(String, String)> {
    match v {
        Value::Dict(d) => {
            if d.values().any(|x| x.as_str().map_or(false, |s| names_path(s, rel))) {
                let s = |k: &str| d.get(k).and_then(Value::as_str).map(str::trim).filter(|x| !x.is_empty());
                let rev = s("revision");
                // a model's id names no dataset, and a dataset's no model
                let kind_keys: &[&str] = if r#type == "dataset" { &["dataset", "dataset_id"] } else { &["model", "model_id"] };
                let with_rev: &[&str] = if rev.is_some() { HF_WITH_REVISION } else { &[] };
                for k in HF_KEYS.iter().chain(kind_keys).chain(with_rev) {
                    if let Some(id) = s(k).filter(|id| hf_href(id, "").is_some() && id.contains('/')) {
                        return Some(("hf".into(), id.to_string() + &rev.map(|r| format!("@{r}")).unwrap_or_default()));
                    }
                }
                for k in URL_KEYS {
                    if let Some(u) = s(k).filter(|u| is_public_url(u)) {
                        return Some(("url".into(), u.to_string()));
                    }
                }
            }
            d.values().find_map(|x| beside(x, rel, r#type))
        }
        Value::List(l) => l.iter().find_map(|x| beside(x, rel, r#type)),
        _ => None,
    }
}

/// The locations with the same place recorded twice said once.
pub fn once(locs: &[Location]) -> Vec<&Location> {
    let mut seen: Vec<(&str, &str)> = Vec::new();
    locs.iter()
        .filter(|l| {
            let k = (l.provider.as_str(), l.r#ref.as_str());
            let new = !seen.contains(&k);
            seen.push(k);
            new
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_local_path_typed_into_words_is_said_as_a_local_path() {
        let said = |t: &str| without_local_paths(t);
        assert_eq!(said("python fetch.py --out /home/ana/data"), "python fetch.py --out <local path>");
        assert_eq!(said("python fetch.py --out=/home/ana/data --n 5"), "python fetch.py --out=<local path> --n 5");
        assert_eq!(said("copied from /srv/lab/share by ana."), "copied from <local path> by ana.");
        assert_eq!(said("the (/tmp/x) split"), "the (<local path>) split");
        assert_eq!(said("from C:\\Users\\ana\\val and ~/imagenet and ../shared/val"), "from <local path> and <local path> and <local path>");
        assert_eq!(said("from \\\\lab-nas\\share and file:///home/ana/x"), "from <local path> and <local path>");
        assert_eq!(said("$HOME/data, ${HOME}/data, %USERPROFILE%\\data and $env:USERPROFILE\\data"), "<local path>, <local path>, <local path> and <local path>");
        assert_eq!(said("-o/home/ana/p --out:/home/ana/q x/../../../home/ana/r"), "-o<local path> --out:<local path> <local path>");
        assert_eq!(said("tar xf \"/opt/lab data/val.tar\" -C '/srv/x y'"), "tar xf \"<local path>\" -C '<local path>'");
        assert_eq!(said("--in=\"/srv/q1\",/home/ana/afterquote"), "--in=\"<local path>\",<local path>");
        assert_eq!(said("python fetch.py >/home/ana/log 2>/home/ana/err; dirs a,/home/ana/c"), "python fetch.py ><local path> 2><local path>; dirs a,<local path>");
        assert_eq!(said("see [the copy](/home/ana/md) and @/home/ana/at and \"$HOME\"/q"), "see [the copy](<local path>) and @<local path> and \"<local path>");
        // words that only hold a slash, an address and a path in the repository stay as typed
        for kept in ["10 GB/s, and/or 1/2 of the images", "https://data.example.org/holdout-v2", "data/val.jsonl", "~5 GB", "a / b", "Deng et al. 2009, MIT license",
                     "costs $5/month", "50% / 50%", "data/../val", "ratio 3:2 at lr=0.1", "\"the quoted words\"", "-v -n 5",
                     "about ~1/3 of the images", "roughly ~10/20 held out", "priced at $k/GPU-hour", "throughput 1200 img /s", "CIFAR-10/100, 50/50"] {
            assert_eq!(said(kept), kept);
        }
    }

    #[test]
    fn a_bundles_resource_says_its_typed_paths_as_local_paths() {
        let data = crate::pyjson::loads(r#"{"id": "recds", "subset": "the /tmp/x split", "acquisition": {"source": "copied from /srv/lab/share by ana", "recipe": "python fetch.py --out /home/ana/data", "url": "https://data.example.org/recds"}, "locations": []}"#).unwrap();
        let clean = without_private_places(data.as_dict().unwrap()).unwrap();
        let text = crate::pyjson::dumps(&Value::Dict(clean));
        for gone in ["/tmp/x", "/srv/lab", "/home/ana"] {
            assert!(!text.contains(gone), "{text}");
        }
        assert!(text.contains("https://data.example.org/recds") && text.contains("the <local path> split"), "{text}");
        // a resource whose words name none is left as it is
        let plain = crate::pyjson::loads(r#"{"id": "val", "subset": "10000 images", "acquisition": {"source": "Deng et al. 2009", "recipe": "python get_data.py --out data/val"}}"#).unwrap();
        assert!(without_private_places(plain.as_dict().unwrap()).is_none());
    }

    #[test]
    fn a_hugging_face_id_is_its_page_and_a_dataset_is_under_datasets() {
        assert_eq!(hf_href("timm/resnet50.a1_in1k", "checkpoint").as_deref(), Some("https://huggingface.co/timm/resnet50.a1_in1k"));
        assert_eq!(hf_href("ana-lab/imagenet-val-int8", "dataset").as_deref(), Some("https://huggingface.co/datasets/ana-lab/imagenet-val-int8"));
        assert_eq!(hf_href("datasets/ana-lab/calib", "checkpoint").as_deref(), Some("https://huggingface.co/datasets/ana-lab/calib"));
        assert_eq!(hf_href("timm/resnet50.a1_in1k@0123abcd", "model").as_deref(), Some("https://huggingface.co/timm/resnet50.a1_in1k/tree/0123abcd"));
        assert_eq!(hf_href("not an id", "model"), None);
        assert_eq!(hf_href("a/b/c/d", "model"), None);
    }

    #[test]
    fn the_hugging_face_cache_path_names_its_id_and_revision() {
        let p = Path::new("/home/ana/.cache/huggingface/hub/models--timm--resnet50.a1_in1k/snapshots/0123abcd9f/model.safetensors");
        assert_eq!(from_hf_cache(p, "checkpoint").as_deref(), Some("timm/resnet50.a1_in1k@0123abcd9f"));
        let d = Path::new("/data/hf/hub/datasets--ana-lab--imagenet-val");
        assert_eq!(from_hf_cache(d, "dataset").as_deref(), Some("ana-lab/imagenet-val"));
        assert_eq!(from_hf_cache(d, "checkpoint").as_deref(), Some("datasets/ana-lab/imagenet-val"));
        assert_eq!(from_hf_cache(Path::new("C:\\Users\\ana\\.cache\\huggingface\\hub\\models--timm--vit"), "model").as_deref(), Some("timm/vit"));
        assert_eq!(from_hf_cache(Path::new("/srv/models/resnet50"), "model"), None);
        // a model's folder read as a dataset keeps its kind, and the cache's folder is never the path's first
        assert_eq!(from_hf_cache(Path::new("/home/ana/hub/models--timm--vit"), "dataset").as_deref(), Some("models/timm/vit"));
        assert_eq!(hf_href("models/timm/vit", "dataset").as_deref(), Some("https://huggingface.co/timm/vit"));
        assert_eq!(from_hf_cache(Path::new("models--timm--vit"), "model"), None);
    }

    #[test]
    fn a_citation_and_a_licence_are_chips_never_the_way_to_get_it() {
        let none = crate::dict! {"kind" => "none", "href" => "", "text" => ""};
        assert_eq!(chips("Deng et al. 2009, MIT license", &none), vec!["Deng et al. 2009", "MIT"]);
        assert_eq!(chips("configs/model.yaml; Apache 2.0", &none), vec!["configs/model.yaml", "Apache 2.0"]);
        assert_eq!(said(&none, &chips("Deng et al. 2009, MIT license", &none)), "no download link \u{b7} Deng et al. 2009 \u{b7} MIT");
    }

    #[test]
    fn a_page_gives_an_address_only_where_it_holds_no_credentials_or_token() {
        assert!(is_public_url("https://data.example.org/imagenet/val.tar"));
        for private in ["https://ana:pw@data.example.org/val.tar", "https://bucket.s3.amazonaws.com/val.tar?X-Amz-Signature=abc", "https://x.org/a#t", "ftp://x.org/a"] {
            assert!(!is_public_url(private), "{private}");
        }
    }

    #[test]
    fn a_path_outside_the_repository_is_one_in_either_systems_form() {
        for p in ["file:///home/ana/data", "file:/home/ana/data", "/home/ana/data", "/Users/ana/data", "C:\\Users\\ana\\data", "C:/Users/ana", "\\\\lab-nas\\share\\x", "~/data", "../x"] {
            assert!(outside(p), "{p}");
        }
        for p in ["data/val.jsonl", "models/x", "."] {
            assert!(!outside(p), "{p}");
        }
    }
}
