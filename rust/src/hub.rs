//! rabbitbrain.ai from the command line: `rb login` (the device-code flow, and the credentials file it keeps, mode 0600,
//! outside the repository and the environment), `rb logout` (revoke and remove), and `rb sync` (a private copy of the
//! record's page in your account, and of the record itself with --with-record). RB_HUB_TOKEN overrides the file, which is what CI uses. Nothing here prints or logs a
//! token. An address that does not answer, and one that answers but not as the hub does, are both said in a sentence
//! that names the address.

use crate::cli::{eprintln_err, stderr_write, Out};
use crate::errors::{RBError, Res};
use crate::http::{self, Failure, Reply};
use crate::ledger::Ledger;
use crate::pyjson::{self, py_str, Dumps};
use crate::value::{Dict, Value};
use std::path::PathBuf;
use std::time::{Duration, Instant};

pub const DEFAULT_HUB: &str = "https://rabbitbrain.ai";

/// Whether rabbitbrain.ai's hosted service is open. While it is not, rb login and rb sync addressed to it send nothing.
const HOSTED_OPEN: bool = true;

/// Whether the hosted service is open for this command: HOSTED_OPEN, unless RB_TEST_HOSTED_CLOSED plays it closed, so
/// the closed path keeps its tests while the service is open.
fn hosted_open() -> bool {
    HOSTED_OPEN && std::env::var_os("RB_TEST_HOSTED_CLOSED").is_none()
}

/// rabbitbrain.ai while its hosted service is not open: said plainly, with what works without it, and nothing sent. A
/// hub named with --hub or RB_HUB_URL is the user's to call.
fn not_open(hub: &str, cmd: &str) -> Res<()> {
    closed_to(hosted_open(), hub, cmd)
}

fn closed_to(open: bool, hub: &str, cmd: &str) -> Res<()> {
    if open || !is_rabbitbrain(hub) {
        return Ok(());
    }
    let what = if cmd == "login" { "no account to sign in to" } else { "no account to keep a copy in" };
    Err(RBError::msg_fix(
        "E_NOT_AVAILABLE",
        format!("rabbitbrain.ai's hosted service is not open yet, so there is {what}; rb {cmd} sent nothing."),
        "The record needs no account: rb page writes its page as one file, rb open shows it in your browser on this machine, and rb export --with-record writes a copy to host anywhere, which rb clone reads.",
    ))
}

/// Whether an address is rabbitbrain.ai's however it is spelled: either scheme, any case, a leading www., a trailing
/// dot, its default port, a path.
fn is_rabbitbrain(hub: &str) -> bool {
    let low = hub.trim().to_ascii_lowercase();
    let rest = low.strip_prefix("https://").or_else(|| low.strip_prefix("http://")).unwrap_or(&low);
    let host = rest.split(['/', '?', '#']).next().unwrap_or_default();
    let host = host.rsplit_once('@').map_or(host, |(_, h)| h);
    let host = host.strip_suffix(":443").or_else(|| host.strip_suffix(":80")).unwrap_or(host).trim_end_matches('.');
    host.strip_prefix("www.").unwrap_or(host) == "rabbitbrain.ai"
}

/// --hub, or RB_HUB_URL when it is set (set to nothing is a usage error, which `address` says), or rabbitbrain.ai.
pub fn hub_of(flag: Option<String>) -> String {
    match flag.filter(|h| !h.is_empty()) {
        Some(h) => h,
        None => std::env::var("RB_HUB_URL").unwrap_or_else(|_| DEFAULT_HUB.to_string()),
    }
}

/// Where rb keeps what it knows of the user's accounts: RB_CONFIG_DIR, or rabbit-brain/ under XDG_CONFIG_HOME, or
/// under ~/.config (on Windows, under %APPDATA%).
pub fn config_dir() -> String {
    let nonempty = |k: &str| std::env::var(k).ok().filter(|v| !v.is_empty());
    if let Some(d) = nonempty("RB_CONFIG_DIR") {
        return crate::cmds::pathlib_str(&d);
    }
    let base = nonempty("XDG_CONFIG_HOME").unwrap_or_else(crate::sys::config_home);
    crate::cmds::pathlib_str(&format!("{base}/rabbit-brain"))
}

pub fn creds_path() -> PathBuf {
    PathBuf::from(crate::cmds::pathlib_str(&format!("{}/credentials.json", config_dir())))
}

/// tempfile.mkdtemp(prefix=...): a new directory of our own under the temporary directory.
fn mkdtemp(prefix: &str) -> std::io::Result<PathBuf> {
    let mut last = std::io::Error::from(std::io::ErrorKind::AlreadyExists);
    for _ in 0..100 {
        let p = std::env::temp_dir().join(format!("{prefix}{:08x}", crate::rng::os_randbits(32)));
        match std::fs::create_dir(&p) {
            Ok(()) => return Ok(p),
            Err(e) => last = e,
        }
    }
    Err(last)
}

/// The credentials file as a mapping of hub to entry: None when a file is there and is not what rb login writes (not
/// text, not JSON, not a mapping), an empty mapping when there is no file.
fn stored() -> Option<Dict> {
    let p = creds_path();
    if !p.exists() {
        return Some(Dict::new());
    }
    match std::fs::read(&p).ok().and_then(|bytes| String::from_utf8(bytes).ok()).and_then(|text| pyjson::loads(&text).ok()) {
        Some(Value::Dict(d)) => Some(d),
        _ => None,
    }
}

/// The same, with a file that is not what rb login writes read as empty: rb login writes it again.
fn read_all() -> Dict {
    stored().unwrap_or_default()
}

fn key(hub: &str) -> String {
    hub.trim_end_matches('/').to_string()
}

pub fn load_token(hub: &str) -> Option<Value> {
    match read_all().get(&key(hub)) {
        Some(Value::Dict(e)) => e.get("token").cloned(),
        _ => None,
    }
}

/// Write the credentials file, readable by its owner alone.
fn write_creds(data: &Dict) -> Res<PathBuf> {
    let p = creds_path();
    let text = pyjson::dumps_with(&Value::Dict(data.clone()), &Dumps::indent(1)).unwrap_or_default() + "\n";
    crate::sys::write_private(&p, text.as_bytes()).map_err(|e| RBError::could_not_write(&e, &p))?;
    Ok(p)
}

fn save_token(hub: &str, token: &Value, handle: &Value) -> Res<PathBuf> {
    let p = creds_path();
    if let Some(parent) = p.parent() {
        std::fs::create_dir_all(parent).map_err(|e| RBError::could_not_write(&e, parent))?;
    }
    let mut data = read_all();
    let created = chrono::Utc::now().format("%Y-%m-%dT%H:%M:%SZ").to_string();
    data.insert(key(hub), crate::obj! {"token" => token.clone(), "handle" => handle.clone(), "created" => created});
    write_creds(&data)
}

fn drop_token(hub: &str) -> Res<bool> {
    let mut data = read_all();
    if data.shift_remove(&key(hub)).is_none() {
        return Ok(false);
    }
    write_creds(&data)?;
    Ok(true)
}

fn unreachable(url: &str, reason: &str) -> RBError {
    RBError::msg("E_HUB_UNREACHABLE", format!("{url} could not be reached: {reason}"))
}

/// The address answered, and not as the hub answers: something else is there, or a hub this rb does not know.
fn odd(url: &str, what: &str) -> RBError {
    RBError::msg("E_HUB_UNREACHABLE", format!("{url} did not answer as the hub does: {what}."))
}

fn not_an_address(what: &str) -> RBError {
    RBError::msg_fix(
        "E_USAGE",
        format!("{what} is not a web address: one starts with https:// (or http://) and names a host."),
        "Give the hub's address in full, with --hub or RB_HUB_URL. Without either, rb uses https://rabbitbrain.ai.",
    )
}

/// A credential that cannot go into a request: the address is fine, what would be sent with it is not.
fn unsendable() -> RBError {
    RBError::msg_fix("E_USAGE", "The credential for this hub cannot be sent: it holds a character no request carries.", "Check RB_HUB_TOKEN, or run rb login again.")
}

/// The hub as it was named: --hub, or RB_HUB_URL, or rabbitbrain.ai. RB_HUB_URL set to nothing names none.
fn named(flag: Option<String>) -> Res<String> {
    let given = hub_of(flag);
    if given.is_empty() {
        return Err(RBError::msg_fix("E_USAGE", "RB_HUB_URL is set to nothing.", "Unset it to use https://rabbitbrain.ai, or give it the hub's address."));
    }
    Ok(given)
}

/// The hub's address as rb calls it, or the usage error for what is not one.
fn address(flag: Option<String>) -> Res<String> {
    let given = named(flag)?;
    let hub = key(&given);
    if !http::is_address(&hub) {
        return Err(not_an_address(&given));
    }
    Ok(hub)
}

/// What an address answered, with the address: rb reads it as the hub's answers are shaped, and says so when it is
/// not shaped that way.
struct Answer {
    url: String,
    body: Value,
}

impl Answer {
    /// A 2xx answer's body, which the hub always sends as JSON.
    fn of(url: &str, body: &[u8]) -> Res<Answer> {
        match pyjson::loads(&String::from_utf8_lossy(body)) {
            Ok(body) => Ok(Answer { url: url.to_string(), body }),
            Err(_) => Err(odd(url, "its answer is not JSON")),
        }
    }
    fn odd(&self, what: &str) -> RBError {
        odd(&self.url, what)
    }
    fn fields(&self) -> Res<&Dict> {
        match &self.body {
            Value::Dict(d) => Ok(d),
            _ => Err(self.odd("its answer is not a JSON object")),
        }
    }
    /// A field the answer may carry.
    fn get(&self, k: &str) -> Res<Option<&Value>> {
        Ok(self.fields()?.get(k))
    }
    /// A field the answer must carry.
    fn item(&self, k: &str) -> Res<&Value> {
        self.fields()?.get(k).ok_or_else(|| self.odd(&format!("its answer has no {k}")))
    }
    /// A count of seconds the answer may carry: a number, or a number written as text.
    fn seconds(&self, k: &str, default: u64) -> Res<u64> {
        let n = match self.get(k)? {
            None | Some(Value::Null) => return Ok(default),
            Some(Value::Int(i)) => Some(*i),
            Some(Value::Bool(b)) => Some(*b as i64),
            Some(Value::Float(f)) if f.is_finite() => Some(f.trunc() as i64),
            Some(Value::Str(s)) => crate::rbyaml::py_strip(s).replace('_', "").parse::<i64>().ok(),
            Some(_) => None,
        };
        // a wait or an expiry is never more than a day, whatever number was sent
        n.map(|n| n.clamp(0, 86_400) as u64).ok_or_else(|| self.odd(&format!("its {k} is not a number")))
    }
}

/// A JSON request to the hub's sign-in endpoints: (status, the answer). An error status comes back with its JSON, or
/// {"error": <its reason>} when it sent none.
fn post(url: &str, body: &Value) -> Res<(u16, Answer)> {
    let headers = [("content-type", "application/json".to_string())];
    match http::request("POST", url, &headers, Some(pyjson::dumps(body).into_bytes()), Duration::from_secs(30)) {
        Ok(Reply { status, reason, body }) => {
            if (200..300).contains(&status) {
                return Ok((status, Answer::of(url, &body)?));
            }
            let said = match pyjson::loads(&String::from_utf8_lossy(&body)) {
                Ok(v @ Value::Dict(_)) => v,
                _ => crate::obj! {"error" => reason},
            };
            Ok((status, Answer { url: url.to_string(), body: said }))
        }
        Err(Failure::Unreachable(reason)) => Err(unreachable(url, &reason)),
        Err(Failure::NotAnAddress) => Err(not_an_address(url)),
        Err(Failure::Unsendable) => Err(unsendable()),
    }
}

/// What the device flow ends in: the token and the handle (when the hub names one), or why there is none.
fn approval(hub: &str, device_code: &Value, mut interval: u64, lasts: u64) -> Res<(String, Option<String>)> {
    let expired = || RBError::msg_fix("E_HUB_REFUSED", "The code expired before it was approved.", "Run rb login again.");
    let deadline = Instant::now() + Duration::from_secs(lasts);
    let mut unavailable = 0;
    while Instant::now() < deadline {
        std::thread::sleep(Duration::from_secs(interval));
        stderr_write(".");
        let (status, ans) = post(&format!("{hub}/api/hub/device/token"), &crate::obj! {"device_code" => device_code.clone()})?;
        // a gateway or a hub that is down for a moment (502, 503, 504) is asked again, three times in a row at most:
        // the code may be approved already, and the next answer gives the token
        if matches!(status, 502..=504) && unavailable < 3 {
            unavailable += 1;
            continue;
        }
        unavailable = 0;
        let err = ans.get("error")?.cloned().unwrap_or(Value::Null);
        match err.as_str() {
            Some("authorization_pending") => continue,
            Some("slow_down") => {
                interval += 5;
                continue;
            }
            Some("access_denied") => return Err(RBError::msg("E_HUB_REFUSED", "Declined on the hub. Nothing was created.")),
            Some("expired_token") => return Err(expired()),
            _ => {}
        }
        if err.truthy() {
            let said = ans.get("error_description")?.map(py_str).unwrap_or_else(|| py_str(&err));
            return Err(RBError::msg("E_HUB_REFUSED", format!("The hub answered: {said}")));
        }
        if !(200..300).contains(&status) {
            return Err(RBError::msg_fix("E_HUB_REFUSED", format!("The hub answered {status} and gave no reason."), "Run rb login again; nothing was stored on this machine."));
        }
        // a credential is text, and there is some of it: anything else is not one to keep
        let token = match ans.item("token")? {
            Value::Str(t) if !t.is_empty() => t.clone(),
            Value::Str(_) => return Err(ans.odd("its token is empty")),
            _ => return Err(ans.odd("its token is not text")),
        };
        let handle = match ans.get("handle")? {
            Some(Value::Str(h)) if !h.is_empty() => Some(h.clone()),
            _ => None,
        };
        return Ok((token, handle));
    }
    Err(expired())
}

/// Ask the hub to revoke a credential: whether it said it did. A success status counts when it comes as the hub's
/// answers do (a JSON object, or nothing at all): a page of something else that says 200 to everything revoked nothing.
fn revoke(hub: &str, token: &Value) -> bool {
    let headers = [("content-type", "application/json".to_string()), ("authorization", format!("Bearer {}", py_str(token)))];
    match http::request("DELETE", &format!("{hub}/api/hub/tokens/current"), &headers, Some(b"{}".to_vec()), Duration::from_secs(30)) {
        Ok(Reply { status, body, .. }) if (200..300).contains(&status) => {
            body.iter().all(u8::is_ascii_whitespace) || matches!(pyjson::loads(&String::from_utf8_lossy(&body)), Ok(Value::Dict(_)))
        }
        _ => false,
    }
}

pub fn cmd_login(hub_flag: Option<String>, out: &mut Out) -> Res<i32> {
    let hub = address(hub_flag)?;
    not_open(&hub, "login")?;
    let (_, start) = post(&format!("{hub}/api/hub/device/code"), &Value::empty_dict())?;
    if !start.fields()?.contains_key("device_code") {
        let detail = start.get("error")?.map(py_str).unwrap_or_else(|| "no detail".into());
        return Err(RBError::msg("E_HUB_REFUSED", format!("The hub did not start a sign-in: {detail}")));
    }
    // the whole answer is read before anything is shown: a code is only shown when the flow it starts can finish
    let user_code = py_str(start.item("user_code")?);
    let fallback = py_str(start.item("verification_uri")?);
    let open = start.get("verification_uri_complete")?.map(py_str).unwrap_or(fallback);
    let interval = start.seconds("interval", 5)?;
    let lasts = start.seconds("expires_in", 600)?;
    let device_code = start.item("device_code")?.clone();
    eprintln_err(&format!("\n  First, this terminal gets a code:\n\n      {user_code}\n"));
    eprintln_err(&format!("  Open {open} on any device; it signs you in first if needed, and approve the code."));
    stderr_write("  Waiting (Ctrl-C stops; nothing is created until you approve) ");
    let approved = approval(&hub, &device_code, interval, lasts);
    eprintln_err(""); // the line of dots ends here, whatever the wait ended in
    let (token, handle) = approved?;
    let path = save_token(&hub, &Value::Str(token), &Value::Str(handle.clone().unwrap_or_default()))?;
    let shown = crate::sys::slashed(&path);
    // elsewhere the file is readable by its owner alone because rb sets it so; on Windows the profile it sits in is
    let kept = if cfg!(windows) { "" } else { " (mode 0600)" };
    out.say(format!("Signed in as {} on {hub}.", handle.as_deref().unwrap_or("you")));
    out.say(format!("The token lives in {shown}{kept}, not in your environment; RB_HUB_TOKEN still overrides it for CI."));
    out.say("rb sync now works here. rb logout revokes and removes it.");
    out.data = crate::dict! {"hub" => hub.clone(), "handle" => handle.map(Value::Str).unwrap_or(Value::Null), "credentials" => shown};
    Ok(0)
}

pub fn cmd_logout(hub_flag: Option<String>, out: &mut Out) -> Res<i32> {
    let hub = key(&named(hub_flag)?);
    if stored().is_none() {
        // rb cannot look for this hub in a file it cannot read, and will not say "nothing stored" of one that may hold a token
        let shown = crate::sys::slashed(&creds_path());
        return Err(RBError::msg_fix(
            "E_USAGE",
            format!("{shown} is not as rb login writes it, so rb cannot find {hub} in it."),
            "Delete the file to remove whatever credential it holds, and revoke at /account if one may have leaked. rb login writes the file again.",
        ));
    }
    let token = load_token(&hub);
    // whatever stops the revoke (the hub out of reach, an answer that is not the hub's), the credential here still goes
    let revoked = token.as_ref().filter(|t| t.truthy()).map_or(false, |t| revoke(&hub, t));
    let dropped = drop_token(&hub)?;
    if !token.as_ref().map_or(false, Value::truthy) && !dropped {
        out.say(format!("Nothing stored for {hub}; nothing to do."));
    } else {
        out.say(
            format!("Logged out of {hub}: credential removed from {}", crate::sys::slashed(&creds_path()))
                + if revoked { " and revoked on the hub." } else { ". The hub was not reachable to revoke it; revoke at /account if it may have leaked." },
        );
    }
    out.data = crate::dict! {"hub" => hub, "revoked" => revoked, "removed" => dropped};
    Ok(0)
}

/// RB_HUB_TOKEN wins (CI); otherwise the credentials file rb login wrote. Never anywhere else.
fn token_for(hub: &str) -> Res<Value> {
    if let Ok(t) = std::env::var("RB_HUB_TOKEN") {
        if !t.is_empty() {
            return Ok(Value::Str(t));
        }
    }
    if let Some(t) = load_token(hub).filter(Value::truthy) {
        return Ok(t);
    }
    // the sentence names the hub that was asked: rabbitbrain.ai, unless --hub or RB_HUB_URL named another
    let (name, login) = if key(hub) == DEFAULT_HUB { ("rabbitbrain.ai".to_string(), "rb login".to_string()) } else { (hub.to_string(), format!("rb login --hub {hub}")) };
    Err(RBError::msg_fix("E_USAGE", format!("No credential for {name} on this machine."), format!("Run {login} once per machine, or set RB_HUB_TOKEN for CI.")))
}

/// What a refusal says to do when the hub said nothing more.
const NO_FIX: &str = "Read the message; the hub says what it needs.";

thread_local! {
    /// The status of the last refusal `call` met, for rb sync --watch, which stops on one that waiting does not change.
    static REFUSED: std::cell::Cell<Option<u16>> = const { std::cell::Cell::new(None) };
}

/// A refusal no wait changes: the credential, the record or the request is what the hub refuses (401, 403, 413, ...),
/// not the moment (408, 409 for another sync of the account in progress, 425, 429, and every 5xx).
fn refused_for_good(status: u16) -> bool {
    (400..500).contains(&status) && !matches!(status, 408 | 409 | 423 | 425 | 429)
}

/// One authenticated call; an error status is the hub refusing, said with its own words when it gave them.
fn call(url: &str, token: &Value, method: &str, body: Option<Vec<u8>>, ctype: Option<&str>) -> Res<Answer> {
    let mut headers: Vec<(&str, String)> = vec![("authorization", format!("Bearer {}", py_str(token)))];
    if let Some(c) = ctype {
        headers.push(("content-type", c.to_string()));
    }
    match http::request(method, url, &headers, body, Duration::from_secs(120)) {
        Ok(Reply { status, reason, body }) => {
            if (200..300).contains(&status) {
                return Answer::of(url, &body);
            }
            let detail = match pyjson::loads(&String::from_utf8_lossy(&body)) {
                Ok(Value::Dict(d)) => d,
                _ => Dict::new(),
            };
            REFUSED.with(|r| r.set(Some(status)));
            let said = detail.get("error").map(py_str).unwrap_or(reason);
            let fix = detail.get("fix").map(py_str).unwrap_or_else(|| NO_FIX.into());
            Err(RBError::msg_fix("E_HUB_REFUSED", format!("The hub answered {status}: {said}"), fix))
        }
        Err(Failure::Unreachable(reason)) => Err(unreachable(url, &reason)),
        Err(Failure::NotAnAddress) => Err(not_an_address(url)),
        Err(Failure::Unsendable) => Err(unsendable()),
    }
}

/// The page as `rb export --zip --no-repo` writes it (the manifest names no remote address or commit of the repository), or with `record` (the address the record will have) the bundle `rb export
/// --zip --with-record --url <url>` writes, built in a folder of its own under the temporary directory that is gone when
/// this returns. The bundle names the code (`--no-repo` left off) only where `names_code` says origin is an address a
/// reader can use, by rb export's rule: a share link's reader who reruns it gets the code from there, and nothing of
/// this machine travels.
/// With the bytes, what a watch compares (`Shown`) of what they hold, and the code the bundle names (its manifest's
/// `repo`), read under the same hold of the record.
fn bundle_bytes(led: &Ledger, record: Option<&str>) -> Res<(Vec<u8>, Shown, Option<Dict>)> {
    let names_code = record.is_some() && names_code(&led.root);
    led.locked(|| {
        let td = mkdtemp("rb-sync-").map_err(|e| RBError::could_not_write(&e, &std::env::temp_dir().join("rb-sync")))?;
        let out = td.join("bundle.zip");
        // the clock's minutes for this build: from just before it to just after, and when rb last looked, read either side
        let (before, looked) = (crate::pytime::now_dt(), crate::journal::Scope::of(led).looked_at);
        let read = crate::export::build(led, Some(&out), true, record, !names_code, record.is_some()).and_then(|said| {
            let clock = Clock::between(before, crate::pytime::now_dt(), [looked, crate::journal::Scope::of(led).looked_at]);
            let bytes = std::fs::read(&out).map_err(|e| RBError::could_not_read(&e, &crate::sys::slashed(&out)))?;
            let entries = crate::zipfile::read_zip(&out).map_err(|e| RBError::msg("E_INTERNAL", format!("rb itself failed: the bundle it wrote does not read ({e:?})")))?;
            let page = entries.iter().find(|(name, _)| name == "bundle/page.html").map(|(_, b)| String::from_utf8_lossy(b).into_owned());
            let page = page.ok_or_else(|| RBError::msg("E_INTERNAL", "rb itself failed: the bundle it wrote has no page.html"))?;
            let code = match said.get("repo") {
                Some(Value::Dict(r)) => Some(r.clone()),
                _ => None,
            };
            Ok((bytes, Shown { digest: led.state_digest()?, page, clock }, code))
        });
        let _ = std::fs::remove_dir_all(&td);
        read
    })
}

/// Whoami, export the page (and with `with_record` the record, with the address it will have), upload privately. The
/// site's answer, with the handle.
fn upload(led: &Ledger, token: &Value, hub: &str, with_record: bool) -> Res<Sent> {
    let who = call(&format!("{hub}/api/hub/whoami"), token, "GET", None, None)?;
    // everything the upload needs of this answer is read first: nothing is sent to an address that is not the hub
    let base = py_str(who.item("base")?);
    let mut endpoint = py_str(who.item("publish")?);
    let handle = who.item("handle")?.clone();
    if !http::is_address(&endpoint) {
        return Err(who.odd("its publish is not a web address"));
    }
    endpoint.push(if endpoint.contains('?') { '&' } else { '?' });
    endpoint.push_str("visibility=private");
    let proj = led.project()?;
    // the record is held first, so a wait for it ends at once on Ctrl-C
    let record = with_record.then(|| format!("{base}/{}/bundle.zip", proj.id));
    let (bytes, shown, code) = led.locked(|| {
        // a signal that would end rb while the bundle is in the temporary directory ends it once that is gone
        let endings = crate::sys::catch_endings();
        let read = bundle_bytes(led, record.as_deref());
        drop(endings);
        if crate::sys::interrupted() {
            crate::sys::end_interrupted();
        }
        read
    })?;
    // nothing is left on disk while it is sent: however rb ends then, nothing is left behind
    let res = call(&endpoint, token, "PUT", Some(bytes), Some("application/zip"))?;
    let mut d = crate::dict! {"handle" => handle};
    for (k, v) in res.fields()? {
        d.insert(k.clone(), v.clone());
    }
    if !d.contains_key("url") {
        return Err(res.odd("its answer has no url"));
    }
    Ok(Sent { said: d, shown, record, code })
}

/// An upload: the site's answer with the handle, what it sent (`Shown`), the address the record went with, which
/// a watch builds its next looks with, and the code the bundle names (its manifest's `repo`).
struct Sent {
    said: Dict,
    shown: Shown,
    record: Option<String>,
    code: Option<Dict>,
}

pub fn cmd_sync(hub_flag: Option<String>, with_record: bool, out: &mut Out) -> Res<i32> {
    let led = Ledger::open(None)?;
    let hub = address(hub_flag)?;
    not_open(&hub, "sync")?;
    let token = token_for(&hub)?;
    let sent = upload(&led, &token, &hub, with_record)?;
    let url = sent.said.get("url").map(py_str).unwrap_or_default();
    out.say(format!("Synced, privately: {url}"));
    for line in synced_words(&led, with_record, sent.code.as_ref(), false) {
        out.say(line);
    }
    for (k, v) in sent.said {
        out.data.insert(k, v);
    }
    out.data.insert("with_record".into(), Value::Bool(with_record));
    // the code the bundle names, as its manifest's repo says it; none for a page alone
    out.data.insert("repo".into(), sent.code.map(Value::Dict).unwrap_or(Value::Null));
    Ok(0)
}

/// What a sync says after its address: who can open the copy, how it is shared and kept current, what went, and what a
/// share link's readers can rerun (only a record sent, once its owner lets them on the site). A watch keeps it current
/// itself, so it says that instead of asking for rb sync again.
fn synced_words(led: &Ledger, with_record: bool, code: Option<&Dict>, watching: bool) -> Vec<String> {
    let (again, updates) = if watching {
        ("rb sync --watch sends it again each time the record changes", "each sync updates what the link shows")
    } else {
        ("run rb sync again after new runs", "rb sync again updates what the link shows")
    };
    let mut said = vec![
        format!("Only you can open it, signed in. It shows the record as computed at this sync; {again}."),
        format!("Share it from its page on the site, with a link you can revoke; {updates}."),
    ];
    if with_record {
        said.push("The record went with its page, and the record in this repository stays the store. Only you can download the record, unless you let a share link's readers rerun it on the site.".into());
        said.push(format!("{} Anyone who can open the copy can read all of it.", crate::export::record_carries(led)));
        said.push(code_words(code));
    } else {
        said.push("Its page went alone: the record stays in this repository, the store, and rb sync --with-record sends it too.".into());
        said.push("A share link's readers can rerun only a sync made with --with-record.".into());
    }
    said
}

/// What a record sent says of its code: in the Rerun panel's words, the code the runs used, at its commit; or, where it
/// names none, the line rb export says then (`export::CODE_UNNAMED`), so the two say one thing in one wording.
fn code_words(code: Option<&Dict>) -> String {
    use crate::shell::strings as w;
    let url = code.and_then(|c| c.get("url")).map(py_str).unwrap_or_default();
    if url.is_empty() {
        return crate::export::CODE_UNNAMED.to_string();
    }
    let commit: String = code.and_then(|c| c.get("commit")).map(py_str).unwrap_or_default().chars().take(10).collect();
    let at = if commit.is_empty() { String::new() } else { format!("{}{commit}", w::AT_COMMIT) };
    format!("{}{url}{at}. Readers who rerun it get the code with the record.", w::CODE_THE_RUNS_USED)
}

/// Whether a record sent names the code: by rb export's one rule (`export::readable_code_address`), over origin's
/// address as the checkout's config writes it, which rb export reads too (an insteadOf's rewrite for this machine's
/// own fetches is this machine's). The address named is the one rb export names, with any credentials, query and
/// fragment taken out.
fn names_code(root: &std::path::Path) -> bool {
    let Ok(r) = std::process::Command::new("git").args(["config", "--get", "remote.origin.url"]).current_dir(root).stdin(std::process::Stdio::null()).output() else {
        return false;
    };
    // read as rb export reads it (git_out): stripped as Python strips, which takes more than Rust's trim
    r.status.success() && crate::export::readable_code_address(crate::rbyaml::py_strip(&String::from_utf8_lossy(&r.stdout))).is_some()
}

/// How often rb sync --watch looks at the record, and the least time between two uploads.
const WATCH_EVERY: Duration = Duration::from_secs(30);
const UPLOAD_EVERY: Duration = Duration::from_secs(60);
/// The waits after one, two, three, four and five or more failures in a row.
const BACK_OFF: [Duration; 5] =
    [Duration::from_secs(30), Duration::from_secs(60), Duration::from_secs(120), Duration::from_secs(300), Duration::from_secs(600)];

/// A wait, as a sentence says it.
fn said_wait(d: Duration) -> String {
    match d.as_secs() {
        s if s < 60 => format!("{s} s"),
        s => format!("{} min", s / 60),
    }
}

/// RB_TEST_SYNC_PACE=<factor> scales every wait of rb sync --watch for the tests, as RB_TEST_NOW pins the clock.
fn paced(d: Duration) -> Duration {
    match std::env::var("RB_TEST_SYNC_PACE").ok().and_then(|v| v.trim().parse::<f64>().ok()) {
        Some(f) if f.is_finite() && f > 0.0 => d.mul_f64(f.min(1.0)),
        _ => d,
    }
}

/// RB_TEST_SYNC_CLOCK=<time>,<seconds> pins the watch's clock at <time> and moves it on by <seconds> before each look,
/// for the tests: a watch whose clock alone moves, a minute a look, sends nothing after its first upload. The clock is
/// RB_TEST_NOW, which every part of rb reads; it is set while no other thread of rb runs.
fn tick_test_clock(looks: u32) {
    let Ok(said) = std::env::var("RB_TEST_SYNC_CLOCK") else { return };
    let Some((start, step)) = said.split_once(',') else { return };
    let (Some(start), Ok(step)) = (crate::pytime::parse_iso(start.trim()), step.trim().parse::<i64>()) else { return };
    let now = start.with_timezone(&chrono::Utc) + chrono::Duration::seconds(step * i64::from(looks));
    std::env::set_var("RB_TEST_NOW", now.format("%Y-%m-%dT%H:%M:%SZ").to_string());
}

/// What a look of the watch comes to: the key it read, then whether it sent.
enum Looked {
    Same,
    Soon(Duration),
    Sent(Shown, Option<String>),
    Failed(RBError),
}

/// What a look of the watch compares: the record's digest, which covers every write, with the page a sync sends and
/// the clock it was made by, so a look sends when the record or what the page says moved, and never for what the page
/// does not show (a heartbeat file the journal lists apart, say). The times the clock alone writes (when the page was
/// made, when rb last looked) are no change, wherever and however the page draws them (`Shown::same`): a page that
/// moved only by the time is not sent again. What an upload sent is read with its bytes, so a change that lands while
/// a look reads is sent once it is whole, and the same page is never sent twice.
struct Shown {
    digest: String,
    page: String,
    clock: Clock,
}

impl Shown {
    /// Whether `now` says what this said: the same record, and the same page but for the times each page's own clock
    /// wrote. Two pages are read side by side, time by time: a time is the clock's on one page when it is one of that
    /// page's clock minutes, and two times differ only by the clock when each is its own page's clock's, in the same
    /// form. A time a write, a source or the record says is compared as it reads, though it fall in the clock's
    /// minute: a note written at 23:10 reads 23:10 on both pages, whatever minute each was made in. A date alone is
    /// never the clock's, and a source that says the very minute of each look moves only with the clock, as the clock
    /// does. A source's time that falls where the clock writes (its minute, or within a second of the build for a time
    /// to the second) reads as the clock's while the clock is there, and is sent once the clock has moved on, within a
    /// minute.
    fn same(&self, now: &Shown) -> bool {
        if self.digest != now.digest {
            return false;
        }
        let (mut was, mut is) = (TIMES.find_iter(&self.page), TIMES.find_iter(&now.page));
        let (mut at_was, mut at_is) = (0, 0);
        loop {
            match (was.next(), is.next()) {
                (None, None) => return self.page[at_was..] == now.page[at_is..],
                (Some(a), Some(b)) => {
                    if self.page[at_was..a.start()] != now.page[at_is..b.start()] {
                        return false;
                    }
                    let clocks = self.clock.wrote(a.as_str()) && now.clock.wrote(b.as_str()) && form(a.as_str()) == form(b.as_str());
                    if a.as_str() != b.as_str() && !clocks {
                        return false;
                    }
                    (at_was, at_is) = (a.end(), b.end());
                }
                _ => return false,
            }
        }
    }
}

/// A date and a time to the minute or finer, with a space or a T between them and any zone after (`2026-10-21 16:59
/// UTC`, `2026-10-21T16:59:58.120Z`): the times a page can say.
static TIMES: std::sync::LazyLock<regex::Regex> = std::sync::LazyLock::new(|| {
    regex::Regex::new(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::(\d{2}(?:\.\d+)?))?(?:Z|\+00:?00| UTC)?").expect("the times' pattern")
});

/// A time's form, its digits left out: two times of one form read alike but for their values.
fn form(t: &str) -> String {
    t.chars().map(|c| if c.is_ascii_digit() { '0' } else { c }).collect()
}

/// When the clock could have written on a page: from just before its build began to just after it ended, and when rb
/// last looked. A time is the clock's by its value and its place, never by the markup around it, so whatever a page
/// draws (a stamp, a footer, a scope line, a new line a later page adds) is held the same way.
struct Clock(Vec<(chrono::DateTime<chrono::Utc>, chrono::DateTime<chrono::Utc>)>);

impl Clock {
    fn between(from: chrono::DateTime<chrono::Utc>, to: chrono::DateTime<chrono::Utc>, looked: impl IntoIterator<Item = Option<String>>) -> Clock {
        let mut spans = vec![(from.min(to), to.max(from))];
        spans.extend(looked.into_iter().flatten().filter_map(|at| crate::pytime::parse_iso(&at)).map(|t| (t.with_timezone(&chrono::Utc), t.with_timezone(&chrono::Utc))));
        Clock(spans)
    }

    /// Whether `time`, one of `TIMES`, is one the clock could have written: a time to the minute, when that minute
    /// meets one of the clock's spans; a time to the second or finer, when it is within a second of one, so a time a
    /// source says to the second is the clock's only if it is the very second the page was made or rb looked.
    fn wrote(&self, time: &str) -> bool {
        let Some(c) = TIMES.captures(time) else { return false };
        let Ok(minute) = chrono::NaiveDateTime::parse_from_str(&format!("{} {}", &c[1], &c[2]), "%Y-%m-%d %H:%M") else { return false };
        let minute = minute.and_utc();
        let second = chrono::Duration::seconds(1);
        match c.get(3) {
            None => self.0.iter().any(|(from, to)| minute <= *to && *from < minute + chrono::Duration::minutes(1)),
            Some(s) => {
                let Ok(secs) = s.as_str().parse::<f64>() else { return false };
                let t = minute + chrono::Duration::milliseconds((secs * 1000.0) as i64);
                self.0.iter().any(|(from, to)| *from - second <= t && t <= *to + second)
            }
        }
    }
}

/// One look: the journal's look, what a sync would send now (`Shown`, of the bundle built as the last upload built it,
/// with the address its record went with), and when it says other than the last upload and a minute has passed since,
/// the upload. The look and the key are taken under rb's catch of the signals that end it, so
/// a signal then ends rb once nothing of the look is half written.
fn look(root: &std::path::Path, hub: &str, with_record: bool, sent: Option<(&Shown, Option<&str>)>, last: Option<Instant>) -> Looked {
    // each look reads the record, its sources and git afresh, as a new command line does: nothing an earlier look worked out holds
    crate::ledger::forget_worked();
    crate::sources::forget_reads();
    let endings = crate::sys::catch_endings();
    let read = Ledger::open(Some(root)).and_then(|led| {
        let _ = crate::journal::scan(&led); // observation is best effort, as on rb open
        match sent {
            // the first look sends: there is nothing to compare it with
            None => Ok((led, None)),
            Some((_, record)) => bundle_bytes(&led, record).map(|(_, k, _)| (led, Some(k))),
        }
    });
    drop(endings);
    if crate::sys::interrupted() {
        crate::sys::end_interrupted();
    }
    let (led, shown) = match read {
        Ok(r) => r,
        Err(e) => return Looked::Failed(e),
    };
    if matches!((sent, &shown), (Some((was, _)), Some(now)) if was.same(now)) {
        return Looked::Same;
    }
    if let Some(left) = last.and_then(|t| paced(UPLOAD_EVERY).checked_sub(t.elapsed())).filter(|d| !d.is_zero()) {
        return Looked::Soon(left);
    }
    // the credential is read again each time: an rb login in another terminal is used at the next upload
    match token_for(hub).and_then(|token| upload(&led, &token, hub, with_record)) {
        Ok(up) => {
            let url = up.said.get("url").map(py_str).unwrap_or_default();
            let at = crate::pytime::now_dt().format("%H:%M UTC");
            eprintln_err(&format!("Synced at {at}, privately: {url}"));
            if sent.is_none() {
                for line in synced_words(&led, with_record, up.code.as_ref(), true) {
                    eprintln_err(&format!("  {line}"));
                }
            }
            Looked::Sent(up.shown, up.record)
        }
        Err(e) => Looked::Failed(e),
    }
}

/// rb sync --watch: rb sync, then again each time the record changes, until it is ended. Every 30 s it looks at the
/// record and the page it would send (`Shown`), and uploads when either moved, at most once a minute, privately. A failure is
/// said in one line and tried again after 30 s, then 1, 2, 5 and 10 min. Ctrl-C, a request to end or a terminal that
/// closed ends it by that signal, with nothing left behind: no bundle is on disk between two uploads.
pub fn cmd_sync_watch(hub_flag: Option<String>, with_record: bool, out: &mut Out) -> Res<i32> {
    if out.json_mode {
        // one envelope is what --json prints, and a watch ends only by a signal, after none
        return Err(RBError::msg_fix(
            "E_USAGE",
            "rb sync --watch says each sync on standard error as it goes and ends only when it is stopped, so it has no one answer for --json.",
            "Run rb sync --watch without --json, or rb sync --json for one sync and its answer.",
        ));
    }
    let led = Ledger::open(None)?;
    let hub = address(hub_flag)?;
    not_open(&hub, "sync")?;
    token_for(&hub)?;
    let root = led.root.clone();
    drop(led);
    eprintln_err("Watching the record: rb sync sends it, privately, each time it changes, at most once a minute. Ctrl-C ends it.");
    let (mut sent, mut last, mut failed) = (None::<(Shown, Option<String>)>, None::<Instant>, 0usize);
    let mut looks = 0u32;
    loop {
        tick_test_clock(looks);
        looks += 1;
        REFUSED.with(|r| r.set(None));
        let wait = match look(&root, &hub, with_record, sent.as_ref().map(|(k, r)| (k, r.as_deref())), last) {
            Looked::Same => {
                failed = 0; // a failure that cleared with nothing to send is over too
                WATCH_EVERY
            }
            Looked::Soon(left) => {
                std::thread::sleep(left);
                continue;
            }
            Looked::Sent(shown, record) => {
                (sent, last, failed) = (Some((shown, record)), Some(Instant::now()), 0);
                WATCH_EVERY
            }
            // the hub refusing the credential or the copy itself: waiting changes nothing, so it ends as rb sync does
            Looked::Failed(e) if REFUSED.with(|r| r.get()).map_or(false, refused_for_good) => return Err(e),
            Looked::Failed(e) => {
                let wait = BACK_OFF[failed.min(BACK_OFF.len() - 1)];
                failed += 1;
                eprintln_err(&format!("Not synced: {} Trying again in {}.", with_fix(&e), said_wait(wait)));
                wait
            }
        };
        std::thread::sleep(paced(wait));
    }
}

/// A failure in one line: its message, and what to do when it says more than the hub's default.
fn with_fix(e: &RBError) -> String {
    let stop = |t: &str| if t.ends_with(['.', '!', '?']) { "" } else { "." }.to_string();
    let said = e.message.trim_end();
    let mut line = format!("{said}{}", stop(said));
    let fix = e.fix.trim_end();
    // a code's own fix (rb's table of errors) says what the code means, not what this failure needs
    let generic = RBError::msg(&e.code, "").fix;
    if !fix.is_empty() && fix != NO_FIX && e.fix != generic {
        line.push_str(&format!(" {fix}{}", stop(fix)));
    }
    line
}


#[cfg(test)]
mod tests {
    use super::{closed_to, Clock, Shown};

    #[test]
    fn rabbitbrain_ai_closed_sends_nothing_and_open_goes_on() {
        let closed = closed_to(false, "https://rabbitbrain.ai", "sync").unwrap_err();
        assert_eq!(closed.code, "E_NOT_AVAILABLE");
        assert!(closed_to(false, "http://127.0.0.1:8787", "sync").is_ok());
        assert!(closed_to(true, "https://www.rabbitbrain.ai/", "login").is_ok());
        assert!(super::HOSTED_OPEN, "0.6.1 opens rabbitbrain.ai");
    }

    fn at(t: &str) -> chrono::DateTime<chrono::Utc> {
        chrono::DateTime::parse_from_rfc3339(t).unwrap().with_timezone(&chrono::Utc)
    }

    /// A page made at `made`, rb having last looked at `looked`, a note written at `wrote` and a source that says `cited`.
    fn shown(clock: &Clock, made: &str, looked: &str, wrote: &str, cited: &str) -> Shown {
        let page = format!("<span class=\"stamp\">{made} UTC · rb 0.6.0</span><summary><span data-live=\"scope_short\">rb last looked at {looked} UTC; rb status says where it looks.</span></summary><li><span class=\"t\">{wrote} UTC</span> note experiment e1</li><p>configs/train.yaml#date now says {cited}</p>");
        Shown { digest: "d".into(), page, clock: Clock(clock.0.clone()) }
    }

    #[test]
    fn a_page_that_moved_only_by_the_clock_reads_the_same() {
        let one = Clock::between(at("2026-10-21T23:10:58Z"), at("2026-10-21T23:10:59Z"), [Some("2026-10-21T23:10:57.004".to_string())]);
        let two = Clock::between(at("2026-10-21T23:11:01Z"), at("2026-10-21T23:11:02Z"), [Some("2026-10-21T23:11:00.5".to_string())]);
        let far = Clock::between(at("2026-11-03T08:01:30Z"), at("2026-11-03T08:01:31Z"), [None]);
        let a = shown(&one, "2026-10-21 23:10", "2026-10-21 23:10", "2026-10-21 23:10", "2026-10-20 09:00");
        // the clock moved, and so the stamp, the scope line and the footer with it; the note written at 23:10 did not
        assert!(a.same(&shown(&two, "2026-10-21 23:11", "2026-10-21 23:11", "2026-10-21 23:10", "2026-10-20 09:00")));
        assert!(a.same(&shown(&far, "2026-11-03 08:01", "2026-10-21 23:11", "2026-10-21 23:10", "2026-10-20 09:00")) == false);
        assert!(a.same(&shown(&far, "2026-11-03 08:01", "2026-11-03 08:01", "2026-10-21 23:10", "2026-10-20 09:00")));
        // a time the clock did not write, which a write, a source or the record says, still moves it: a write in the
        // clock's minute moves the record's digest, and a time on the page that is not its own clock's is read as it is
        let mut wrote = shown(&two, "2026-10-21 23:11", "2026-10-21 23:11", "2026-10-21 23:11", "2026-10-20 09:00");
        wrote.digest = "d2".into();
        assert!(!a.same(&wrote));
        assert!(!a.same(&shown(&two, "2026-10-21 23:11", "2026-10-21 23:11", "2026-10-21 23:09", "2026-10-20 09:00")));
        assert!(!a.same(&shown(&one, "2026-10-21 23:10", "2026-10-21 23:10", "2026-10-21 23:10", "2026-10-22 09:00")));
        let mut other = shown(&one, "2026-10-21 23:10", "2026-10-21 23:10", "2026-10-21 23:10", "2026-10-20 09:00");
        other.page = other.page.replace("note experiment", "notes experiment");
        assert!(!a.same(&other));
        other.page = a.page.clone();
        other.digest = "e".into();
        assert!(!a.same(&other));
        // a time to the second is the clock's only within a second of the build or the look: a source's time in the
        // clock's minute, changed by a second, moves it
        let to_the_second = |c: &Clock, made: &str, cited: &str| Shown {
            digest: "d".into(),
            page: format!("<time datetime=\"{made}\"></time><p>configs/eval.yaml#cut now says {cited}</p>"),
            clock: Clock(c.0.clone()),
        };
        let pinned = Clock::between(at("2026-10-21T16:59:30Z"), at("2026-10-21T16:59:30.400Z"), [None]);
        let later = Clock::between(at("2026-10-21T17:00:30Z"), at("2026-10-21T17:00:31Z"), [None]);
        let first = to_the_second(&pinned, "2026-10-21T16:59:30.120Z", "2026-10-21T16:59:01Z");
        assert!(first.same(&to_the_second(&later, "2026-10-21T17:00:30.900Z", "2026-10-21T16:59:01Z")));
        assert!(!first.same(&to_the_second(&pinned, "2026-10-21T16:59:30.120Z", "2026-10-21T16:59:02Z")));
        assert!(!first.same(&to_the_second(&later, "2026-10-21T17:00:30.900Z", "2026-10-21T16:59:02Z")));
        // a date alone is never the clock's
        let date = |d: &str, c: &Clock| Shown { digest: "d".into(), page: format!("split_date {d}"), clock: Clock(c.0.clone()) };
        assert!(!date("2026-10-21", &one).same(&date("2026-10-22", &two)));
    }
}
