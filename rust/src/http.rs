//! The little HTTP rb speaks on its own: the hub's sign-in, sync and revoke calls, and the download of a bundle someone
//! names. A 4xx or 5xx comes back with its status and body; a connection that fails comes back with the system's own
//! words for why ("Connection refused", "Name or service not known", "timed out"); what is not a web address is
//! refused before anything connects.

use std::io::Read;
use std::time::Duration;

/// An answer from a server, whatever its status.
pub struct Reply {
    pub status: u16,
    /// the reason phrase urllib's HTTPError carries
    pub reason: String,
    pub body: Vec<u8>,
}

/// What stops a request before any answer.
pub enum Failure {
    /// no answer came: why, in the system's words ("Connection refused", "timed out"), and whether the connection went
    Unreachable(Why),
    /// not a web address at all: no http:// or https:// in front, or no host after it
    NotAnAddress,
    /// the address is one, and what was to be sent with it cannot be (a header value no request carries)
    Unsendable,
}

/// Why no answer came: the words said for it, and whether a connection was refused, reset, aborted, closed before its
/// answer or timed out (`went`), as against an address that cannot answer at all (a host no resolver knows, a
/// certificate this machine does not accept). `went` is read from the kind of error the HTTP and TLS libraries give,
/// never from their words, so a new wording of theirs changes nothing.
#[derive(Debug, Clone, PartialEq)]
pub struct Why {
    pub words: String,
    pub went: bool,
}

impl Why {
    fn went(words: impl Into<String>) -> Why {
        Why { words: words.into(), went: true }
    }
    fn stays(words: impl Into<String>) -> Why {
        Why { words: words.into(), went: false }
    }
}

impl std::ops::Deref for Why {
    type Target = str;
    fn deref(&self) -> &str {
        &self.words
    }
}

impl std::fmt::Display for Why {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.words)
    }
}

impl PartialEq<str> for Why {
    fn eq(&self, other: &str) -> bool {
        self.words == other
    }
}

impl PartialEq<&str> for Why {
    fn eq(&self, other: &&str) -> bool {
        self.words == *other
    }
}

/// The kinds of error that say a connection was there and went, or did not come in time.
fn went_kind(kind: std::io::ErrorKind) -> bool {
    use std::io::ErrorKind::*;
    matches!(kind, ConnectionRefused | ConnectionReset | ConnectionAborted | BrokenPipe | TimedOut | UnexpectedEof)
}

/// Why an I/O step stopped, with whether its connection went.
fn io_why(e: &std::io::Error) -> Why {
    // WSAESHUTDOWN, a send on a socket the other side shut, is a broken pipe that std may not give a kind of its own
    let shut = cfg!(windows) && e.raw_os_error() == Some(10058);
    Why { words: io_text(e), went: went_kind(e.kind()) || shut }
}

/// A system error by its code, in the system's words: "Connection refused".
fn errno_text(code: i32) -> String {
    crate::sys::os_code_words(code)
}

/// What the resolver says of a host it cannot find: "Name or service not known".
fn gai_text(host: &str) -> String {
    crate::sys::unresolved(host)
}

/// Why reading an answer, or writing it to a file, stopped.
fn io_text(e: &std::io::Error) -> String {
    if e.raw_os_error().is_none() && e.kind() == std::io::ErrorKind::TimedOut {
        return "timed out".into();
    }
    crate::sys::os_words(e)
}

/// The scheme and host of a URL, as urllib splits them; None when urllib would not take it as a URL.
fn scheme_host(url: &str) -> Option<(String, String)> {
    let (scheme, rest) = url.split_once(':')?;
    if scheme.is_empty() || scheme.contains('/') {
        return None;
    }
    let host = rest.strip_prefix("//").map(|r| r.split(|c| matches!(c, '/' | '?' | '#')).next().unwrap_or("")).unwrap_or("");
    let host = host.rsplit('@').next().unwrap_or(host);
    let host = if let Some(h) = host.strip_prefix('[') { h.split(']').next().unwrap_or(h) } else { host.split(':').next().unwrap_or(host) };
    Some((scheme.to_ascii_lowercase(), host.to_string()))
}

/// Why reading an answer's body stopped: ureq's own reason (a timeout) when it is ureq's, else the system's words.
fn read_text(e: &std::io::Error, host: &str) -> Why {
    match e.get_ref().and_then(|inner| inner.downcast_ref::<ureq::Error>()) {
        Some(inner) => reason_of(inner, host),
        None => io_why(e),
    }
}

fn reason_of(e: &ureq::Error, host: &str) -> Why {
    match e {
        // the resolver's failure, as std words it: say what the resolver itself says
        ureq::Error::Io(io) if io.raw_os_error().is_none() && io.to_string().starts_with("failed to lookup address information") => Why::stays(gai_text(host)),
        // a connection closed before its answer comes as UnexpectedEof, over http ("Peer disconnected") and https
        // (TLS's words for a close with no close_notify) alike
        ureq::Error::Io(io) => io_why(io),
        ureq::Error::Timeout(_) => Why::went("timed out"),
        ureq::Error::HostNotFound => Why::stays(gai_text(host)),
        ureq::Error::ConnectionFailed => Why::went(errno_text(crate::sys::CONNECTION_REFUSED)),
        ureq::Error::Tls(t) => Why::stays(t.to_string()),
        ureq::Error::Rustls(e) => Why::stays(certificate_words(&e.to_string())),
        // a proxy that refused the tunnel: "Tunnel connection failed: 403 Forbidden"
        ureq::Error::ConnectProxyFailed(why) => Why::stays(match why.strip_prefix("proxy server responded ").and_then(|r| r.split('/').next()).and_then(|c| c.parse::<u16>().ok()) {
            Some(code) => {
                let reason = ureq::http::StatusCode::from_u16(code).ok().and_then(|c| c.canonical_reason()).unwrap_or("");
                format!("Tunnel connection failed: {code} {reason}").trim_end().to_string()
            }
            None => why.clone(),
        }),
        other => Why::stays(other.to_string()),
    }
}

/// A certificate that was not accepted, in a sentence: the TLS library names its reason by a word of its own
/// ("invalid peer certificate: UnknownIssuer"), which is said here in plain words.
fn certificate_words(shown: &str) -> String {
    let Some(why) = shown.strip_prefix("invalid peer certificate: ") else { return shown.to_string() };
    let why = if why.starts_with("UnknownIssuer") {
        "it is not from an authority this machine trusts"
    } else if why.starts_with("Expired") {
        "it has expired"
    } else if why.starts_with("NotValidYet") {
        "it is not valid yet"
    } else if why.starts_with("NotValidForName") {
        "it is for another name"
    } else if why.starts_with("Revoked") {
        "it was revoked"
    } else {
        "this machine does not accept it"
    };
    format!("the certificate there was not accepted: {why}")
}

/// Each step of a call waits at most `timeout`; reading the answer's body, at most `body` in all.
fn agent(timeout: Option<Duration>, body: Option<Duration>) -> ureq::Agent {
    let tls = ureq::tls::TlsConfig::builder().root_certs(ureq::tls::RootCerts::PlatformVerifier).build();
    ureq::Agent::config_builder()
        .http_status_as_error(false)
        .tls_config(tls)
        .user_agent(format!("rb/{}", crate::version()))
        .accept("")
        .timeout_connect(timeout)
        .timeout_send_request(timeout)
        .timeout_send_body(timeout)
        .timeout_recv_response(timeout)
        .timeout_recv_body(body)
        .build()
        .into()
}

/// The host of a web address, before anything connects: an address rb calls starts with http:// or https:// and names
/// a host.
fn check(url: &str) -> Result<String, Failure> {
    match scheme_host(url) {
        Some((scheme, host)) if (scheme == "http" || scheme == "https") && !host.is_empty() => Ok(host),
        _ => Err(Failure::NotAnAddress),
    }
}

/// Whether `url` is an address rb can call.
pub fn is_address(url: &str) -> bool {
    check(url).is_ok()
}

/// An address as it may be kept and shown: without the credentials (`user:password@`) it may carry, whatever its
/// scheme, with a git remote helper's prefix (`hg::`) and the scheme kept as written; an http or https address also
/// without its query and fragment, where a signed link keeps its signature. What has no `://` (a path, an scp-style
/// remote) is kept as it is.
pub fn shareable(url: &str) -> String {
    let Some((front, rest)) = url.split_once("://") else { return url.to_string() };
    // the credentials run to the last @ before the first / after the scheme: a ? or a # in them leaves none behind
    let authority = rest.find('/').map_or(rest, |end| &rest[..end]);
    let host = authority.rfind('@').map_or(authority, |at| &authority[at + 1..]);
    // a password holding "/" leaves an @ past the first /: what comes before that / is no host[:digits] ("user:pa",
    // "jo@x.com:tok"), or it is a user and a password ("user:p@ss"), so the credentials run to the last @ before the
    // first ? or # after the next @, and no part of the password is said
    let password = authority.find('@').is_some_and(|at| authority[..at].contains(':'));
    let unsure = (host.contains(':') && !is_host_port(host)) || password;
    let rest = match rest[authority.len()..].find('@') {
        Some(next) if unsure => {
            let first = authority.len() + next;
            let end = rest[first..].find(['?', '#']).map_or(rest.len(), |e| first + e);
            &rest[rest[..end].rfind('@').map_or(0, |at| at + 1)..]
        }
        _ => &rest[authority.len() - host.len()..],
    };
    let scheme = front.rsplit("::").next().unwrap_or(front);
    let web = scheme.eq_ignore_ascii_case("http") || scheme.eq_ignore_ascii_case("https");
    let rest = if web { rest.split(['?', '#']).next().unwrap_or("") } else { rest };
    format!("{front}://{rest}")
}

/// What rb clone says in place of an address it cannot read safely (`said`).
pub const UNREADABLE: &str = "an address rb cannot read safely";

/// An address as rb clone says and keeps it: `shareable`'s, but where an @ follows the first /, ? or # after the
/// scheme, a password or a token may hold one of them and its host cannot be told apart from it, so no address is
/// said, only `UNREADABLE`: never a wrong host and never a part of a password or a token. A plain address with an @ in
/// its path or its query is unsaid too: the accepted cost.
pub fn said(url: &str) -> String {
    match url.split_once("://") {
        Some((_, rest)) => {
            let authority = rest.find(['/', '?', '#']).map_or(rest, |end| &rest[..end]);
            if rest[authority.len()..].contains('@') {
                UNREADABLE.to_string()
            } else {
                shareable(url)
            }
        }
        None => url.to_string(),
    }
}

/// Whether an address's authority reads as a host and an optional :port of digits (`lab.example:8443`, `[::1]:8000`),
/// as git and curl read it.
fn is_host_port(authority: &str) -> bool {
    let (name, port) = match authority.strip_prefix('[') {
        Some(v6) => match v6.split_once(']') {
            Some((inside, "")) => (inside, None),
            Some((inside, after)) => match after.strip_prefix(':') {
                Some(p) => (inside, Some(p)),
                None => return false,
            },
            None => return false,
        },
        None => match authority.rsplit_once(':') {
            Some((n, p)) => (n, Some(p)),
            None => (authority, None),
        },
    };
    !name.is_empty() && (authority.starts_with('[') || !name.contains(['[', ']', ':'])) && port.map_or(true, |p| !p.is_empty() && p.chars().all(|c| c.is_ascii_digit()))
}

/// One request; its answer whatever the status, or why there was none.
pub fn request(method: &str, url: &str, headers: &[(&str, String)], body: Option<Vec<u8>>, timeout: Duration) -> Result<Reply, Failure> {
    let host = check(url)?;
    if url.parse::<ureq::http::Uri>().is_err() {
        return Err(Failure::NotAnAddress);
    }
    // the hub's answers are small: one is read within four times the call's time in all (a total, not a bound on how
    // long it goes quiet), so one that stops partway is given up like one that never starts
    let agent = agent(Some(timeout), Some(timeout * 4));
    let mut req = ureq::http::Request::builder().method(method).uri(url);
    for (k, v) in headers {
        req = req.header(*k, v.as_str());
    }
    // the address is one: what is left to go wrong in making the request is a header that cannot be sent
    let Ok(req) = req.body(body.unwrap_or_default()) else {
        return Err(Failure::Unsendable);
    };
    let resp = agent.run(req).map_err(|e| Failure::Unreachable(reason_of(&e, &host)))?;
    let status = resp.status();
    let mut out: Vec<u8> = Vec::new();
    resp.into_body()
        .into_with_config()
        .limit(u64::MAX)
        .reader()
        .read_to_end(&mut out)
        .map_err(|e| Failure::Unreachable(read_text(&e, &host)))?;
    Ok(Reply { status: status.as_u16(), reason: status.canonical_reason().unwrap_or("").to_string(), body: out })
}

/// What the thread reading a download hands over, in order.
enum Got {
    /// the server answered with a success, and its body follows
    Started,
    Bytes(Vec<u8>),
    End,
    /// the answer declares a body past the limit, and none of it is read
    TooBig,
    Failed(Failure),
}

/// The answer to a GET, read into `got` as it arrives; Ok once it is all read, or once nobody takes it any more. Each
/// step waits at most `quiet` and the body is read for at most `whole` in all, so the thread reading it always ends.
fn read_answer(url: &str, host: &str, quiet: Duration, whole: Duration, limit: u64, got: &std::sync::mpsc::SyncSender<Got>) -> Result<(), Failure> {
    let resp = agent(Some(quiet), Some(whole)).get(url).call().map_err(|e| Failure::Unreachable(reason_of(&e, host)))?;
    let status = resp.status();
    if !status.is_success() {
        return Err(Failure::Unreachable(Why::stays(status.canonical_reason().unwrap_or(""))));
    }
    if resp.body().content_length().map_or(false, |n| n > limit) {
        let _ = got.send(Got::TooBig);
        return Ok(());
    }
    if got.send(Got::Started).is_err() {
        return Ok(());
    }
    let mut reader = resp.into_body().into_with_config().limit(u64::MAX).reader();
    let mut buf = vec![0u8; 1 << 20];
    loop {
        match reader.read(&mut buf) {
            Ok(0) => return Ok(()),
            Ok(n) if got.send(Got::Bytes(buf[..n].to_vec())).is_err() => return Ok(()),
            Ok(_) => {}
            Err(e) => return Err(Failure::Unreachable(read_text(&e, host))),
        }
    }
}

/// GET a URL into `dest`, refusing past `limit` bytes. Err(None) when the limit was passed, or when the answer declares
/// a body past it (before anything is written); Err(Some(reason)) when it could not be fetched, a 4xx or 5xx included
/// (its reason is the status phrase), when the answer brings nothing for `quiet` once it has begun or is not whole
/// `whole` after it was asked for, redirects and a slow head included ("timed out"), and when rb is interrupted under
/// `sys::catch_endings` ("interrupted").
pub fn download(url: &str, dest: &std::path::Path, limit: u64, quiet: Duration, whole: Duration) -> Result<(), Option<Failure>> {
    use std::sync::mpsc::RecvTimeoutError;
    let host = check(url).map_err(Some)?;
    let asked = std::time::Instant::now();
    let (tx, rx) = std::sync::mpsc::sync_channel::<Got>(4);
    let given = url.to_string();
    // the network is read on a thread of its own, so rb is never stuck in a read: a thread still waiting on a server
    // when this returns ends by the bounds it reads under, or with rb
    let reader = std::thread::spawn(move || {
        let last = match read_answer(&given, &host, quiet, whole, limit, &tx) {
            Ok(()) => Got::End,
            Err(failure) => Got::Failed(failure),
        };
        let _ = tx.send(last);
    });
    let failed = |e: std::io::Error| Some(Failure::Unreachable(io_why(&e)));
    let mut file: Option<std::fs::File> = None;
    let mut total: u64 = 0;
    let mut heard: Option<std::time::Instant> = None;
    loop {
        if crate::sys::interrupted() {
            return Err(Some(Failure::Unreachable(Why::stays("interrupted"))));
        }
        if asked.elapsed() >= whole {
            return Err(Some(Failure::Unreachable(Why::went("timed out"))));
        }
        match rx.recv_timeout(Duration::from_millis(100)) {
            Ok(Got::Started) => {
                file = Some(std::fs::File::create(dest).map_err(failed)?);
                heard = Some(std::time::Instant::now());
            }
            Ok(Got::Bytes(chunk)) => {
                heard = Some(std::time::Instant::now());
                total += chunk.len() as u64;
                if total > limit {
                    return Err(None);
                }
                if let Some(f) = file.as_mut() {
                    std::io::Write::write_all(f, &chunk).map_err(failed)?;
                }
            }
            Ok(Got::End) => return Ok(()),
            Ok(Got::TooBig) => return Err(None),
            Ok(Got::Failed(failure)) => return Err(Some(failure)),
            Err(RecvTimeoutError::Timeout) if heard.map_or(false, |t| t.elapsed() >= quiet) => {
                return Err(Some(Failure::Unreachable(Why::went("timed out"))));
            }
            Err(RecvTimeoutError::Timeout) => {}
            // the thread always says how it ended, so one that ended without a word met a fault of rb's own: said as one
            Err(RecvTimeoutError::Disconnected) => match reader.join() {
                Err(fault) => std::panic::resume_unwind(fault),
                Ok(()) => return Err(Some(Failure::Unreachable(Why::stays("the download stopped")))),
            },
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    /// A server on this machine that answers one GET with `head` and `body`, then holds the connection open for `hold`.
    fn answer_once(head: &str, body: Vec<u8>, hold: Duration) -> String {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let head = head.to_string();
        std::thread::spawn(move || {
            let Ok((mut s, _)) = listener.accept() else { return };
            let (mut asked, mut buf) = (Vec::new(), [0u8; 1024]);
            while !asked.windows(4).any(|w| w == b"\r\n\r\n") {
                match s.read(&mut buf) {
                    Ok(0) | Err(_) => return,
                    Ok(n) => asked.extend_from_slice(&buf[..n]),
                }
            }
            let _ = s.write_all(head.as_bytes()).and_then(|()| s.write_all(&body)).and_then(|()| s.flush());
            std::thread::sleep(hold);
        });
        format!("http://127.0.0.1:{port}/bundle.zip")
    }

    /// A server on this machine that answers every request with `pieces`, each written once its wait is over, then
    /// keeps the connection until the client lets go of it, which it says on the channel returned.
    fn serve(pieces: Vec<(Duration, Vec<u8>)>) -> (String, std::sync::mpsc::Receiver<()>) {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let (let_go, gone) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            for conn in listener.incoming() {
                let (Ok(mut s), pieces, let_go) = (conn, pieces.clone(), let_go.clone()) else { return };
                std::thread::spawn(move || {
                    let (mut asked, mut buf) = (Vec::new(), [0u8; 1024]);
                    while !asked.windows(4).any(|w| w == b"\r\n\r\n") {
                        match s.read(&mut buf) {
                            Ok(0) | Err(_) => return,
                            Ok(n) => asked.extend_from_slice(&buf[..n]),
                        }
                    }
                    for (wait, piece) in pieces {
                        std::thread::sleep(wait);
                        if s.write_all(&piece).and_then(|()| s.flush()).is_err() {
                            return;
                        }
                    }
                    while matches!(s.read(&mut buf), Ok(n) if n > 0) {}
                    let _ = let_go.send(());
                });
            }
        });
        (format!("http://127.0.0.1:{port}/bundle.zip"), gone)
    }

    fn temp_dest() -> (std::path::PathBuf, std::path::PathBuf) {
        let dir = std::env::temp_dir().join(format!("rb-http-test-{}-{:08x}", std::process::id(), crate::rng::os_randbits(32)));
        std::fs::create_dir_all(&dir).unwrap();
        (dir.join("bundle.zip"), dir)
    }

    /// An answer that goes quiet partway is given up once nothing has come for the time allowed, however long its
    /// connection stays open; one that arrives is written whole, and one past the limit is refused.
    #[test]
    fn a_download_that_goes_quiet_is_given_up() {
        let (dest, dir) = temp_dest();
        let long = Duration::from_secs(60);
        let quiet = answer_once("HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n", vec![1u8; 1000], Duration::from_secs(20));
        let asked = std::time::Instant::now();
        let got = download(&quiet, &dest, 1 << 20, Duration::from_millis(500), long);
        assert!(matches!(&got, Err(Some(Failure::Unreachable(why))) if why == "timed out"));
        assert!(asked.elapsed() < Duration::from_secs(10), "{:?}", asked.elapsed());
        let whole = answer_once("HTTP/1.1 200 OK\r\nContent-Length: 5000\r\n\r\n", vec![2u8; 5000], Duration::ZERO);
        assert!(download(&whole, &dest, 1 << 20, Duration::from_secs(10), long).is_ok());
        assert_eq!(std::fs::read(&dest).unwrap(), vec![2u8; 5000]);
        // a body that declares no length is counted as it comes
        let past = answer_once("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1388\r\n", [vec![3u8; 5000], b"\r\n0\r\n\r\n".to_vec()].concat(), Duration::ZERO);
        assert!(matches!(download(&past, &dest, 4000, Duration::from_secs(10), long), Err(None)));
        let missing = answer_once("HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n", Vec::new(), Duration::ZERO);
        assert!(matches!(download(&missing, &dest, 1 << 20, Duration::from_secs(10), long), Err(Some(Failure::Unreachable(why))) if why == "Not Found"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// However a server holds a download that never goes quiet for long (a byte at a time, a slow head, redirect after
    /// redirect), it is given up once it is not whole in the time it has in all, and the thread reading it lets go of
    /// the connection by then too.
    #[test]
    fn a_download_is_given_up_once_its_whole_time_is_spent() {
        let (dest, dir) = temp_dest();
        let (quiet, whole) = (Duration::from_secs(5), Duration::from_millis(1500));
        let ticks: Vec<(Duration, Vec<u8>)> = (0..60).map(|_| (Duration::from_millis(100), vec![0u8])).collect();
        let head = |wait: Duration| (wait, b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n".to_vec());
        let again = (Duration::from_millis(400), b"HTTP/1.1 302 Found\r\nLocation: /again\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".to_vec());
        for (what, pieces) in [("a byte at a time", [vec![head(Duration::ZERO)], ticks].concat()), ("a slow head", vec![head(Duration::from_secs(20))]), ("redirects", vec![again])] {
            let (url, _) = serve(pieces);
            let asked = std::time::Instant::now();
            let got = download(&url, &dest, 1 << 20, quiet, whole);
            assert!(matches!(&got, Err(Some(Failure::Unreachable(why))) if why == "timed out"), "{what}");
            assert!(asked.elapsed() < Duration::from_secs(4), "{what}: {:?}", asked.elapsed());
        }
        let (url, gone) = serve(vec![head(Duration::ZERO), (Duration::ZERO, vec![1u8; 1000])]);
        assert!(matches!(download(&url, &dest, 1 << 20, quiet, whole), Err(Some(Failure::Unreachable(why))) if why == "timed out"));
        assert!(gone.recv_timeout(Duration::from_secs(5)).is_ok(), "the connection is still held");
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// An answer that declares a body past the limit is refused before anything of it is read or written.
    #[test]
    fn a_download_declared_past_the_limit_is_refused_before_a_byte_is_written() {
        let (dest, dir) = temp_dest();
        let (url, _) = serve(vec![(Duration::ZERO, b"HTTP/1.1 200 OK\r\nContent-Length: 629145600\r\n\r\n".to_vec())]);
        let asked = std::time::Instant::now();
        assert!(matches!(download(&url, &dest, 1 << 20, Duration::from_secs(5), Duration::from_secs(60)), Err(None)));
        assert!(asked.elapsed() < Duration::from_secs(4), "{:?}", asked.elapsed());
        assert!(!dest.exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// An address leaves out the credentials it carries, whatever its scheme and however they are written, keeping a
    /// git remote helper's prefix and the scheme as given; an http or https one leaves out its query and fragment too.
    /// What has no `://` is kept as it is.
    #[test]
    fn an_address_is_kept_without_what_it_carried_for_the_download() {
        for (given, kept) in [
            ("https://user:sekrit-token@github.com/lab/proj.git", "https://github.com/lab/proj.git"),
            ("http://alice:s3cret@127.0.0.1:8000/pub.zip", "http://127.0.0.1:8000/pub.zip"),
            ("https://b.s3.amazonaws.com/pub.zip?X-Amz-Signature=s3cret&X-Amz-Credential=k", "https://b.s3.amazonaws.com/pub.zip"),
            ("https://lab.example/inv/bundle.zip#top", "https://lab.example/inv/bundle.zip"),
            ("https://lab.example?token=s3cret", "https://lab.example"),
            ("HTTPS://Lab.example/b.zip", "HTTPS://Lab.example/b.zip"),
            ("HtTpS://alice:s3cret@Lab.example/b.zip?sig=s3cret#top", "HtTpS://Lab.example/b.zip"),
            ("http://[::1]:8000/pub.zip", "http://[::1]:8000/pub.zip"),
            ("http://alice:s3cret@[::1]:8080/pub.zip?sig=s3cret#top", "http://[::1]:8080/pub.zip"),
            ("https://lab.example:8443/inv/b.zip?sig=s3cret", "https://lab.example:8443/inv/b.zip"),
            ("https://lab.example/u/x@y/b.zip", "https://lab.example/u/x@y/b.zip"),
            ("https://alice@lab.example/u/x@y/b.zip?s=1", "https://lab.example/u/x@y/b.zip"),
            ("https://alice:pa?ss@host/b.zip", "https://host/b.zip"),
            ("https://alice:pa#ss@host/b.zip", "https://host/b.zip"),
            ("https://alice:pa?ss@host", "https://host"),
            ("https://user:pa/ss@host/repo.git", "https://host/repo.git"),
            ("https://user:pa/s?s@host/repo.git?x=1", "https://host/repo.git"),
            ("https://user:pa/ss@host/repo.git?next=a@b", "https://host/repo.git"),
            ("https://user:pa%2Fss@host/repo.git", "https://host/repo.git"),
            ("ssh://git:pa/ss@host/r.git", "ssh://host/r.git"),
            ("https://jo@x.com:tok/en@host/repo.git", "https://host/repo.git"),
            ("https://user:p@ss/x@host/r", "https://host/r"),
            // a host and a port of digits, then a path holding @, is the address git reads: kept whole
            ("https://ci:1234/x@host/r", "https://ci:1234/x@host/r"),
            ("https://lab.example:8443/u/x@y/b.zip", "https://lab.example:8443/u/x@y/b.zip"),
            ("http://[::1]:8000/u/x@y", "http://[::1]:8000/u/x@y"),
            ("hg::https://alice:pw@host/repo", "hg::https://host/repo"),
            ("gcrypt::https://alice:pw@github.com/org/repo.git", "gcrypt::https://github.com/org/repo.git"),
            ("hg::https://alice:pw@host/repo?x=1#f", "hg::https://host/repo"),
            ("git_ssh://alice:pw@host/r.git", "git_ssh://host/r.git"),
            ("ssh://git@github.com/lab/proj.git", "ssh://github.com/lab/proj.git"),
            ("ftp://alice:pw@host/f.zip?x=1#f", "ftp://host/f.zip?x=1#f"),
            ("file:///home/me/c#proj/repo.git", "file:///home/me/c#proj/repo.git"),
            ("s3://bucket/pub.zip?versionId=3", "s3://bucket/pub.zip?versionId=3"),
            ("git@github.com:lab/proj.git", "git@github.com:lab/proj.git"),
            ("/home/me/bundle", "/home/me/bundle"),
            ("C:/work/bundle.zip", "C:/work/bundle.zip"),
            ("javascript:alert(document.domain)/b", "javascript:alert(document.domain)/b"),
            ("exports/a://b?x", "exports/a://b?x"),
        ] {
            assert_eq!(shareable(given), kept, "{given}");
        }
    }

    /// rb clone says no address where an @ follows the first /, ? or # at all (the orchestrator, Oct 31 00:05 +08, for
    /// the /; MP-1031-03 for a ? or #, where shareable names a wrong host), and says every other one as `shareable` does.
    #[test]
    fn an_address_rb_cannot_read_safely_is_not_said() {
        for given in [
            "https://ci:1234/x@host/r",
            "https://user:pa/ss@host/u/x@y/r",
            "https://host:/u/x@y",
            "https://user:pa/ss@host/repo.git",
            "https://user:pw@host/repo.git?next=a@b",
            "https://alice@lab.example/u/x@y/b.zip",
            "https://jo@x.com:tok/en@host/repo.git",
            "ssh://git:pa/ss@host/r.git",
            "http://[::1]:8000/u/x@y",
            "https://user:pw@host?sig=a@b",
            "https://alice:pa?ss@host/b.zip",
            // a token with a / never reaches a record's parent, and a plain @ in a path is unsaid with it
            "https://tok/en@host/r",
            "https://lab.example/u/x@y/b.zip",
            "https://lab.example/b.zip?next=a@b",
            // shareable reads the host after that @ ("https://b"); git and curl read lab.example
            "https://lab.example?next=a@b",
            "http://tok?en@127.0.0.1:9/r",
        ] {
            assert_eq!(said(given), UNREADABLE, "{given}");
        }
        // no @ past the first /, ? or #: said as shareable says it
        for given in ["https://user:pw@host/r.git?sig=s3cret", "https://lab.example/u/b.zip", "https://lab.example?next=ab", "git@github.com:lab/proj.git", "/home/me/bundle"] {
            assert_eq!(said(given), shareable(given), "{given}");
        }
    }

    /// A connection that was there and went (refused, closed before its answer, reset, a broken pipe, timed out) is
    /// told by the kind of error, whatever words the system or the libraries give it; a host no resolver knows, a
    /// network that cannot be reached and a status phrase are not.
    #[test]
    fn a_connection_that_went_is_told_by_its_kind_and_an_address_that_cannot_answer_is_not() {
        let closes = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = closes.local_addr().unwrap().port();
        std::thread::spawn(move || {
            if let Ok((mut s, _)) = closes.accept() {
                let mut buf = [0u8; 4096];
                let _ = std::io::Read::read(&mut s, &mut buf);
            }
        });
        let got = request("POST", &format!("http://127.0.0.1:{port}/api/hub/device/token"), &[], Some(b"{}".to_vec()), Duration::from_secs(5));
        assert!(matches!(&got, Err(Failure::Unreachable(why)) if why.went), "closed before its answer");
        let refused = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = refused.local_addr().unwrap().port();
        drop(refused);
        let got = request("GET", &format!("http://127.0.0.1:{port}/"), &[], None, Duration::from_secs(5));
        assert!(matches!(&got, Err(Failure::Unreachable(why)) if why.went), "refused");
        let got = request("GET", "http://hub.invalid/", &[], None, Duration::from_secs(5));
        assert!(matches!(&got, Err(Failure::Unreachable(why)) if !why.went), "a host no resolver knows");
        #[cfg(unix)]
        let (went, stays) = ([libc::ECONNREFUSED, libc::ECONNRESET, libc::ECONNABORTED, libc::EPIPE, libc::ETIMEDOUT], libc::ENETUNREACH);
        #[cfg(windows)]
        let (went, stays) = ([10061, 10054, 10053, 10058, 10060], 10051); // WSAECONNREFUSED, WSAECONNRESET, WSAECONNABORTED, WSAESHUTDOWN, WSAETIMEDOUT; WSAENETUNREACH
        for code in went {
            assert!(io_why(&std::io::Error::from_raw_os_error(code)).went, "{code}");
        }
        assert!(!io_why(&std::io::Error::from_raw_os_error(stays)).went);
        assert!(io_why(&std::io::Error::from(std::io::ErrorKind::UnexpectedEof)).went);
        assert!(io_why(&std::io::Error::new(std::io::ErrorKind::UnexpectedEof, "peer closed connection without sending TLS close_notify")).went);
    }

    /// An answer from the hub that goes quiet partway through is given up within the call's time, like one that never
    /// begins.
    #[test]
    fn an_answer_that_goes_quiet_is_given_up() {
        let quiet = answer_once("HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n", b"{\"url\": ".to_vec(), Duration::from_secs(20));
        let asked = std::time::Instant::now();
        let got = request("GET", &quiet, &[], None, Duration::from_millis(500));
        assert!(matches!(&got, Err(Failure::Unreachable(why)) if why == "timed out"));
        assert!(asked.elapsed() < Duration::from_secs(10), "{:?}", asked.elapsed());
    }

    /// An answer from the hub that keeps coming is read whole when it takes longer than the call's time, up to four
    /// times that in all.
    #[test]
    fn an_answer_that_keeps_coming_is_read_whole() {
        let pieces = [(Duration::ZERO, b"HTTP/1.1 200 OK\r\nContent-Length: 8\r\n\r\n".to_vec())].into_iter();
        let (url, _) = serve(pieces.chain((0..4).map(|_| (Duration::from_millis(300), b"{}".to_vec()))).collect());
        let got = request("GET", &url, &[], None, Duration::from_millis(500));
        assert!(matches!(&got, Ok(Reply { status: 200, body, .. }) if body == b"{}{}{}{}"));
    }
}
