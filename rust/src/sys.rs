//! What differs between the systems rb runs on (Linux, macOS, Windows), in one place: a file's stamp, the lock, the
//! user's home and name, what an OS error says, the terminal, Ctrl-C, a program left to run on its own. Everything
//! else in rb is the same code on all three; the record itself is the same bytes (paths in it are written with `/`,
//! lines end in `\n`).

use std::path::{Path, PathBuf};

// ---------------------------------------------------------------- files: when, which, how they are written

/// A file's modification time in nanoseconds since 1970.
#[cfg(unix)]
pub fn mtime_ns(md: &std::fs::Metadata) -> i128 {
    use std::os::unix::fs::MetadataExt;
    md.mtime() as i128 * 1_000_000_000 + md.mtime_nsec() as i128
}

#[cfg(windows)]
pub fn mtime_ns(md: &std::fs::Metadata) -> i128 {
    since_epoch(md.modified().ok())
}

#[cfg(windows)]
fn since_epoch(t: Option<std::time::SystemTime>) -> i128 {
    match t.map(|t| t.duration_since(std::time::UNIX_EPOCH)) {
        Some(Ok(d)) => d.as_nanos() as i128,
        Some(Err(e)) => -(e.duration().as_nanos() as i128),
        None => 0,
    }
}

/// When a file last changed on disk at all (its bytes, name or mode), in nanoseconds: a time no one can set back, so a
/// rewrite that put the modification time back still shows. 0 where the system keeps none.
#[cfg(unix)]
pub fn ctime_ns(_path: &Path, md: &std::fs::Metadata) -> i128 {
    use std::os::unix::fs::MetadataExt;
    md.ctime() as i128 * 1_000_000_000 + md.ctime_nsec() as i128
}

/// On Windows the file's change time (FILE_BASIC_INFO's ChangeTime), which a write moves and setting the file's times
/// does not put back.
#[cfg(windows)]
pub fn ctime_ns(path: &Path, _md: &std::fs::Metadata) -> i128 {
    use std::os::windows::fs::OpenOptionsExt;
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Storage::FileSystem::{FileBasicInfo, GetFileInformationByHandleEx, FILE_BASIC_INFO, FILE_FLAG_BACKUP_SEMANTICS};
    // access 0: the attributes alone, so a file someone else holds open still answers
    let Ok(f) = std::fs::OpenOptions::new().access_mode(0).custom_flags(FILE_FLAG_BACKUP_SEMANTICS).open(path) else { return 0 };
    let mut info: FILE_BASIC_INFO = unsafe { std::mem::zeroed() };
    let size = std::mem::size_of::<FILE_BASIC_INFO>() as u32;
    if unsafe { GetFileInformationByHandleEx(f.as_raw_handle() as _, FileBasicInfo, &mut info as *mut FILE_BASIC_INFO as *mut _, size) } == 0 {
        return 0;
    }
    // 100 ns ticks from 1601; 11644473600 seconds separate that from 1970
    (info.ChangeTime as i128 - 116_444_736_000_000_000) * 100
}

/// The same time as whole seconds and the nanoseconds past them.
pub fn mtime_parts(md: &std::fs::Metadata) -> (i64, u32) {
    let ns = mtime_ns(md);
    (ns.div_euclid(1_000_000_000) as i64, ns.rem_euclid(1_000_000_000) as u32)
}

/// What tells one state of a file from the next without reading it: (modification time, size, which file it is). rb
/// replaces a file by renaming a new one over it, so the third part changes on every write even when the clock and
/// the size do not: the inode, or on Windows the file's index on its volume.
#[cfg(unix)]
pub fn file_stamp(path: &Path) -> Option<(i64, u64, u64)> {
    use std::os::unix::fs::MetadataExt;
    std::fs::metadata(path).ok().map(|md| (md.mtime() * 1_000_000_000 + md.mtime_nsec(), md.size(), md.ino()))
}

#[cfg(windows)]
pub fn file_stamp(path: &Path) -> Option<(i64, u64, u64)> {
    use std::os::windows::fs::OpenOptionsExt;
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Storage::FileSystem::{GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION, FILE_FLAG_BACKUP_SEMANTICS};
    // access 0: the attributes alone are asked for, so a file someone else holds open still answers; the flag lets a
    // directory open too
    let f = std::fs::OpenOptions::new().access_mode(0).custom_flags(FILE_FLAG_BACKUP_SEMANTICS).open(path).ok()?;
    let mut info: BY_HANDLE_FILE_INFORMATION = unsafe { std::mem::zeroed() };
    if unsafe { GetFileInformationByHandle(f.as_raw_handle() as _, &mut info) } == 0 {
        return None;
    }
    // FILETIME counts 100 ns from 1601; 11644473600 seconds separate that from 1970
    let ticks = ((info.ftLastWriteTime.dwHighDateTime as i64) << 32) | info.ftLastWriteTime.dwLowDateTime as i64;
    let mtime = (ticks - 116_444_736_000_000_000) * 100;
    let size = ((info.nFileSizeHigh as u64) << 32) | info.nFileSizeLow as u64;
    let index = ((info.nFileIndexHigh as u64) << 32) | info.nFileIndexLow as u64;
    Some((mtime, size, index))
}

/// The permission bits a zip entry carries for a file.
#[cfg(unix)]
pub fn mode(path: &Path) -> u32 {
    use std::os::unix::fs::MetadataExt;
    std::fs::metadata(path).map(|m| m.mode()).unwrap_or(0o100644)
}

#[cfg(windows)]
pub fn mode(_path: &Path) -> u32 {
    0o100644 // Windows keeps no such bits: a plain file anyone may read, as rb's own files are elsewhere
}

/// Give `dst` the access and modification times of `src` (shutil.copy2's second half).
#[cfg(unix)]
pub fn copy_times(src: &Path, dst: &Path) {
    use std::os::unix::fs::MetadataExt;
    if let Ok(md) = std::fs::metadata(src) {
        set_times(dst, (md.atime(), md.atime_nsec() as u32), (md.mtime(), md.mtime_nsec() as u32));
    }
}

#[cfg(windows)]
pub fn copy_times(src: &Path, dst: &Path) {
    let Ok(md) = std::fs::metadata(src) else { return };
    let (Ok(a), Ok(m)) = (md.accessed(), md.modified()) else { return };
    if let Ok(f) = std::fs::OpenOptions::new().write(true).open(dst) {
        let _ = f.set_times(std::fs::FileTimes::new().set_accessed(a).set_modified(m));
    }
}

/// Set a file's access and modification times, each as (seconds since 1970, nanoseconds); a file that cannot be
/// reached for it keeps its own.
#[cfg(unix)]
pub fn set_times(path: &Path, accessed: (i64, u32), modified: (i64, u32)) {
    let times = [
        libc::timespec { tv_sec: accessed.0 as libc::time_t, tv_nsec: accessed.1 as _ },
        libc::timespec { tv_sec: modified.0 as libc::time_t, tv_nsec: modified.1 as _ },
    ];
    if let Ok(c) = std::ffi::CString::new(path.to_string_lossy().as_bytes()) {
        unsafe {
            libc::utimensat(libc::AT_FDCWD, c.as_ptr(), times.as_ptr(), 0);
        }
    }
}

#[cfg(windows)]
pub fn set_times(path: &Path, accessed: (i64, u32), modified: (i64, u32)) {
    let at = |(secs, nanos): (i64, u32)| std::time::UNIX_EPOCH + std::time::Duration::new(secs.max(0) as u64, nanos);
    if let Ok(f) = std::fs::OpenOptions::new().write(true).open(path) {
        let _ = f.set_times(std::fs::FileTimes::new().set_accessed(at(accessed)).set_modified(at(modified)));
    }
}

/// A new file's permissions before it takes its place under its real name: readable as the rest of the checkout is.
#[cfg(unix)]
pub fn readable_as_checkout(tmp: &Path) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    let umask = unsafe {
        let m = libc::umask(0o022);
        libc::umask(m);
        m
    };
    std::fs::set_permissions(tmp, std::fs::Permissions::from_mode(0o666 & !(umask as u32)))
}

#[cfg(windows)]
pub fn readable_as_checkout(_tmp: &Path) -> std::io::Result<()> {
    Ok(()) // a new file takes its folder's permissions there
}

/// Rename `tmp` over `path`. On Windows a file another program has open (an editor, a virus scanner, the search
/// indexer) cannot be replaced for as long as it is held, which is usually a moment: tried again for about two
/// seconds before the refusal stands.
pub fn replace<A: AsRef<Path>, B: AsRef<Path>>(tmp: A, path: B) -> std::io::Result<()> {
    let (tmp, path) = (tmp.as_ref(), path.as_ref());
    #[cfg(windows)]
    {
        let mut wait = std::time::Duration::from_millis(1);
        for _ in 0..11 {
            match std::fs::rename(tmp, path) {
                // 5 access denied, 32 sharing violation, 33 lock violation: someone holds the file
                Err(e) if matches!(e.raw_os_error(), Some(5 | 32 | 33)) => {
                    std::thread::sleep(wait);
                    wait *= 2;
                }
                other => return other,
            }
        }
    }
    std::fs::rename(tmp, path)
}

/// Whether files can be made in the folder `dir` now: a sandbox can keep a folder read-only that its permission bits
/// call writable (an agent's sandbox leaves git's folder so), so a file is made there and taken away again. A preview
/// writes nothing and asks only whether the folder is there.
pub fn can_write(dir: &Path) -> bool {
    if !dir.is_dir() {
        return false;
    }
    if crate::dry::active() {
        return true;
    }
    let probe = dir.join(format!(".rb-write-{}-{:08x}.tmp", std::process::id(), crate::rng::os_randbits(32)));
    match std::fs::OpenOptions::new().write(true).create_new(true).open(&probe) {
        Ok(f) => {
            drop(f);
            let _ = std::fs::remove_file(&probe);
            true
        }
        Err(_) => false,
    }
}

/// Remove a file. On Windows a file another program holds open (a virus scanner, the search indexer) is tried again for
/// about two seconds, as `replace` does, before the refusal stands.
pub fn remove(path: &Path) -> std::io::Result<()> {
    #[cfg(windows)]
    {
        let mut wait = std::time::Duration::from_millis(1);
        for _ in 0..11 {
            match std::fs::remove_file(path) {
                Err(e) if matches!(e.raw_os_error(), Some(5 | 32 | 33)) => {
                    std::thread::sleep(wait);
                    wait *= 2;
                }
                other => return other,
            }
        }
    }
    std::fs::remove_file(path)
}

/// The part of a path Windows will not let a file or folder be called: one of the names it keeps for devices (con,
/// nul, aux, prn, com1 to com9, lpt1 to lpt9), with or without an extension. None on other systems, and for a path
/// that has none.
pub fn device_name(path: &Path) -> Option<String> {
    if !cfg!(windows) {
        return None;
    }
    path.components().filter_map(|c| match c {
        std::path::Component::Normal(x) => Some(x.to_string_lossy().into_owned()),
        _ => None,
    }).find(|name| {
        let stem = name.split('.').next().unwrap_or("").trim_end().to_ascii_lowercase();
        matches!(stem.as_str(), "con" | "prn" | "aux" | "nul") || ((stem.starts_with("com") || stem.starts_with("lpt")) && stem.len() == 4 && stem.as_bytes()[3].is_ascii_digit() && stem.as_bytes()[3] != b'0')
    })
}

/// Why the system would refuse this process a write at `path`, whose nearest directory there is `dir`: a new file put
/// in its place, or with `append` bytes added to the file. Unix asks access(2), which knows permissions and a file
/// system mounted read-only. Windows lets anyone write in a folder marked read-only, and no one change a file so marked.
#[cfg(unix)]
pub fn write_refused(dir: &Path, path: &Path, append: bool) -> Option<std::io::Error> {
    use std::os::unix::ffi::OsStrExt;
    let (at, mode) = if append && path.is_file() { (path, libc::W_OK) } else { (dir, libc::W_OK | libc::X_OK) };
    let c = std::ffi::CString::new(at.as_os_str().as_bytes()).ok()?;
    (unsafe { libc::access(c.as_ptr(), mode) } != 0).then(std::io::Error::last_os_error)
}

#[cfg(windows)]
pub fn write_refused(_dir: &Path, path: &Path, _append: bool) -> Option<std::io::Error> {
    let md = std::fs::metadata(path).ok()?;
    (md.is_file() && md.permissions().readonly()).then(|| std::io::Error::from_raw_os_error(5))
}

/// Write a file only its owner may read (the credentials). On Windows a file under the user's profile is already
/// theirs alone by the profile's own permissions.
pub fn write_private(path: &Path, data: &[u8]) -> std::io::Result<()> {
    use std::io::Write;
    let mut o = std::fs::OpenOptions::new();
    o.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.mode(0o600);
    }
    let mut f = o.open(path)?;
    f.write_all(data)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))?;
    }
    Ok(())
}

/// The record's lock: the file opened (made if need be) and held exclusively until it is dropped. One writer at a
/// time across processes; None when the file cannot be opened, and then nothing is held.
pub fn lock(path: &Path) -> Option<std::fs::File> {
    let mut o = std::fs::OpenOptions::new();
    o.read(true).write(true).create(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.mode(0o644);
    }
    let f = o.open(path).ok()?;
    let _ = f.lock();
    Some(f)
}

/// The same lock for a write rb only previews: the file opened to read, when it is there, and never made.
pub fn lock_existing(path: &Path) -> Option<std::fs::File> {
    let f = std::fs::File::open(path).ok()?;
    let _ = f.lock();
    Some(f)
}

/// The path with every link followed, as the system names it. On Windows the system's answer comes in its long form
/// (`\\?\C:\work`), which git, Python and most programs handed a path do not take: said the usual way (`C:\work`).
pub fn canonicalize<P: AsRef<Path>>(p: P) -> std::io::Result<PathBuf> {
    let c = std::fs::canonicalize(p)?;
    #[cfg(windows)]
    {
        use std::path::{Component, Prefix};
        let mut parts = c.components();
        if let Some(Component::Prefix(pre)) = parts.next() {
            let head: Option<PathBuf> = match pre.kind() {
                Prefix::VerbatimDisk(letter) => Some(PathBuf::from(format!("{}:\\", letter as char))),
                Prefix::VerbatimUNC(server, share) => Some(PathBuf::from(format!("\\\\{}\\{}\\", server.to_string_lossy(), share.to_string_lossy()))),
                _ => None,
            };
            if let Some(mut out) = head {
                for part in parts {
                    if let Component::Normal(x) = part {
                        out.push(x);
                    }
                }
                return Ok(out);
            }
        }
    }
    Ok(c)
}

/// A path as rb writes one, in the record and to the user: with `/` between its parts on every system. A record
/// made on one system reads on another, and what rb prints can be pasted into any shell (a backslash is an escape in
/// the one most coding agents use on Windows); git says paths the same way there.
pub fn slashed<P: AsRef<Path>>(p: P) -> String {
    let s = p.as_ref().to_string_lossy();
    if cfg!(windows) {
        s.replace('\\', "/")
    } else {
        s.into_owned()
    }
}

/// A path someone typed, with the separators of this system read as `/` (on Windows `configs\train.yaml` is
/// `configs/train.yaml`; elsewhere a backslash is a character of the name and stays).
pub fn typed(text: &str) -> String {
    if cfg!(windows) {
        text.replace('\\', "/")
    } else {
        text.to_string()
    }
}

/// A directory no project's modules sit in, for asking a Python about itself.
pub fn neutral_dir() -> PathBuf {
    if cfg!(windows) {
        std::env::temp_dir()
    } else {
        PathBuf::from("/")
    }
}

/// One part of a zip entry's name, as it may be written under the directory a bundle unpacks into; None for a part
/// that names nothing (`.`), climbs out (`..`) or is empty. On Windows the characters a file name cannot hold become
/// `_` and trailing dots and spaces go, as Python's zipfile cleans a name there.
pub fn zip_part(part: &str) -> Option<String> {
    let cleaned: String = if cfg!(windows) {
        let swapped: String = part.chars().map(|c| if ":<>|\"?*".contains(c) { '_' } else { c }).collect();
        swapped.trim_end_matches(|c| c == ' ' || c == '.').to_string()
    } else {
        part.to_string()
    };
    if cleaned.is_empty() || cleaned == "." || cleaned == ".." {
        None
    } else {
        Some(cleaned)
    }
}

/// The parts of a zip entry's name: split at `/`, and on Windows at `\` too (there it separates directories, and a
/// name that carries one must not climb out of where the bundle unpacks).
pub fn zip_parts(name: &str) -> Vec<String> {
    let is_sep = |c: char| c == '/' || (cfg!(windows) && c == '\\');
    name.split(is_sep).filter_map(zip_part).collect()
}

// ---------------------------------------------------------------- the user: home, name, where their settings live

/// The user's home directory: $HOME, or the account's; on Windows their profile.
#[cfg(unix)]
pub fn home() -> PathBuf {
    if let Some(h) = std::env::var_os("HOME") {
        return PathBuf::from(h);
    }
    unsafe {
        let pw = libc::getpwuid(libc::getuid());
        if !pw.is_null() && !(*pw).pw_dir.is_null() {
            return PathBuf::from(std::ffi::CStr::from_ptr((*pw).pw_dir).to_string_lossy().into_owned());
        }
    }
    PathBuf::from("/")
}

#[cfg(windows)]
pub fn home() -> PathBuf {
    let set = |k: &str| std::env::var_os(k).filter(|v| !v.is_empty());
    if let Some(h) = set("USERPROFILE") {
        return PathBuf::from(h);
    }
    if let (Some(d), Some(p)) = (set("HOMEDRIVE"), set("HOMEPATH")) {
        let mut both = d;
        both.push(p);
        return PathBuf::from(both);
    }
    set("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("C:\\"))
}

/// Where a user's settings for programs live when nothing says otherwise: ~/.config, or %APPDATA% on Windows.
pub fn config_home() -> String {
    #[cfg(windows)]
    {
        if let Some(a) = std::env::var("APPDATA").ok().filter(|v| !v.is_empty()) {
            return slashed(&a);
        }
    }
    format!("{}/.config", slashed(&home()))
}

/// The account's name from the system, when no variable of the environment gives it.
#[cfg(unix)]
pub fn account_name() -> Option<String> {
    unsafe {
        let pw = libc::getpwuid(libc::getuid());
        if !pw.is_null() && !(*pw).pw_name.is_null() {
            return Some(std::ffi::CStr::from_ptr((*pw).pw_name).to_string_lossy().into_owned());
        }
    }
    None
}

#[cfg(windows)]
pub fn account_name() -> Option<String> {
    None // USERNAME is always set there, and the caller has read it
}

// ---------------------------------------------------------------- what the system says

/// What the system says of an error, in its own words and nothing else: "No such file or directory", "Permission
/// denied"; on Windows "The system cannot find the file specified". No code and no bracket: the sentence rb puts
/// around it names the file or the address.
pub fn os_words(e: &std::io::Error) -> String {
    let shown = e.to_string();
    let text = shown.split(" (os error").next().unwrap_or(&shown);
    text.trim_end_matches(|c: char| c == '.' || c.is_whitespace()).to_string()
}

/// The same, for a system error code.
pub fn os_code_words(code: i32) -> String {
    os_words(&std::io::Error::from_raw_os_error(code))
}

/// The code of "nothing is listening there".
#[cfg(unix)]
pub const CONNECTION_REFUSED: i32 = libc::ECONNREFUSED;
#[cfg(windows)]
pub const CONNECTION_REFUSED: i32 = 10061; // WSAECONNREFUSED

/// What the resolver says of a host it cannot find: "Name or service not known" (on Windows "No such host is known").
#[cfg(unix)]
pub fn unresolved(host: &str) -> String {
    const UNKNOWN: &str = "Name or service not known";
    let Ok(c) = std::ffi::CString::new(host) else { return UNKNOWN.into() };
    let mut hints: libc::addrinfo = unsafe { std::mem::zeroed() };
    hints.ai_socktype = libc::SOCK_STREAM;
    let mut res: *mut libc::addrinfo = std::ptr::null_mut();
    let rc = unsafe { libc::getaddrinfo(c.as_ptr(), std::ptr::null(), &hints, &mut res) };
    if rc == 0 {
        unsafe { libc::freeaddrinfo(res) };
        return UNKNOWN.into();
    }
    unsafe { std::ffi::CStr::from_ptr(libc::gai_strerror(rc)).to_string_lossy().into_owned() }
}

#[cfg(windows)]
pub fn unresolved(_host: &str) -> String {
    os_code_words(11001) // WSAHOST_NOT_FOUND
}

/// The machine, when no Python is there to say it as platform.platform() does: "Linux-6.1.0-x86_64".
#[cfg(unix)]
pub fn platform() -> String {
    unsafe {
        let mut u: libc::utsname = std::mem::zeroed();
        if libc::uname(&mut u) == 0 {
            let s = |a: &[libc::c_char]| std::ffi::CStr::from_ptr(a.as_ptr()).to_string_lossy().into_owned();
            return format!("{}-{}-{}", s(&u.sysname), s(&u.release), s(&u.machine));
        }
    }
    std::env::consts::OS.to_string()
}

#[cfg(windows)]
pub fn platform() -> String {
    use windows_sys::Wdk::System::SystemServices::RtlGetVersion;
    use windows_sys::Win32::System::SystemInformation::OSVERSIONINFOW;
    let machine = match std::env::consts::ARCH {
        "x86_64" => "AMD64",
        "aarch64" => "ARM64",
        other => other,
    };
    let mut v: OSVERSIONINFOW = unsafe { std::mem::zeroed() };
    v.dwOSVersionInfoSize = std::mem::size_of::<OSVERSIONINFOW>() as u32;
    if unsafe { RtlGetVersion(&mut v) } == 0 {
        return format!("Windows-{}.{}.{}-{machine}", v.dwMajorVersion, v.dwMinorVersion, v.dwBuildNumber);
    }
    format!("Windows-{machine}")
}

/// The local date and time of a moment given in seconds since 1970: (year, month, day, hour, minute, second).
#[cfg(unix)]
pub fn local_time(secs: i64) -> (i32, u32, u32, u32, u32, u32) {
    let mut tm: libc::tm = unsafe { std::mem::zeroed() };
    let t: libc::time_t = secs as libc::time_t;
    unsafe {
        libc::localtime_r(&t, &mut tm);
    }
    (tm.tm_year + 1900, (tm.tm_mon + 1) as u32, tm.tm_mday as u32, tm.tm_hour as u32, tm.tm_min as u32, tm.tm_sec as u32)
}

#[cfg(windows)]
pub fn local_time(secs: i64) -> (i32, u32, u32, u32, u32, u32) {
    use chrono::{Datelike, TimeZone, Timelike};
    match chrono::Local.timestamp_opt(secs, 0).single() {
        Some(t) => (t.year(), t.month(), t.day(), t.hour(), t.minute(), t.second()),
        None => (1980, 1, 1, 0, 0, 0),
    }
}

/// Seconds since 1970, now.
pub fn now_secs() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
}

/// Random bytes from the operating system; false when it gave none.
#[cfg(unix)]
pub fn random_bytes(buf: &mut [u8]) -> bool {
    use std::io::Read;
    std::fs::File::open("/dev/urandom").map_or(false, |mut f| f.read_exact(buf).is_ok())
}

#[cfg(windows)]
pub fn random_bytes(buf: &mut [u8]) -> bool {
    use windows_sys::Win32::Security::Cryptography::{BCryptGenRandom, BCRYPT_USE_SYSTEM_PREFERRED_RNG};
    unsafe { BCryptGenRandom(std::ptr::null_mut(), buf.as_mut_ptr(), buf.len() as u32, BCRYPT_USE_SYSTEM_PREFERRED_RNG) == 0 }
}

// ---------------------------------------------------------------- the terminal

/// Whether standard input (0), output (1) or error (2) is a terminal.
pub fn isatty(fd: i32) -> bool {
    use std::io::IsTerminal;
    match fd {
        0 => std::io::stdin().is_terminal(),
        1 => std::io::stdout().is_terminal(),
        2 => std::io::stderr().is_terminal(),
        _ => false,
    }
}

/// The width of the terminal standard output is, in columns.
#[cfg(unix)]
pub fn terminal_columns() -> Option<usize> {
    unsafe {
        let mut ws: libc::winsize = std::mem::zeroed();
        if libc::ioctl(1, libc::TIOCGWINSZ, &mut ws) == 0 && ws.ws_col > 0 {
            return Some(ws.ws_col as usize);
        }
    }
    None
}

#[cfg(windows)]
pub fn terminal_columns() -> Option<usize> {
    use windows_sys::Win32::System::Console::{GetConsoleScreenBufferInfo, GetStdHandle, CONSOLE_SCREEN_BUFFER_INFO, STD_OUTPUT_HANDLE};
    unsafe {
        let mut info: CONSOLE_SCREEN_BUFFER_INFO = std::mem::zeroed();
        if GetConsoleScreenBufferInfo(GetStdHandle(STD_OUTPUT_HANDLE), &mut info) != 0 {
            let columns = info.srWindow.Right as i32 - info.srWindow.Left as i32 + 1;
            if columns > 0 {
                return Some(columns as usize);
            }
        }
    }
    None
}

// ---------------------------------------------------------------- Ctrl-C, and the other signals that end a program

static INTERRUPTED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
/// the signal noted, so rb can end by it (`end_interrupted`)
#[cfg(unix)]
static SIGNAL: std::sync::atomic::AtomicI32 = std::sync::atomic::AtomicI32::new(0);

#[cfg(unix)]
extern "C" fn on_interrupt(sig: libc::c_int) {
    SIGNAL.store(sig, std::sync::atomic::Ordering::SeqCst);
    INTERRUPTED.store(true, std::sync::atomic::Ordering::SeqCst);
}

#[cfg(windows)]
unsafe extern "system" fn on_interrupt(event: u32) -> windows_sys::core::BOOL {
    use windows_sys::Win32::System::Console::{CTRL_BREAK_EVENT, CTRL_C_EVENT};
    if event == CTRL_C_EVENT || event == CTRL_BREAK_EVENT {
        INTERRUPTED.store(true, std::sync::atomic::Ordering::SeqCst);
        return 1; // taken: rb finishes what it is doing and ends by itself
    }
    0
}

/// While this is held, Ctrl-C does not end rb: it is noted (`interrupted()`), so rb can finish properly (a server
/// closes, a watched command's exit is still journalled). Dropped, Ctrl-C is what it was.
pub struct Interrupts {
    #[cfg(unix)]
    previous: Vec<(libc::c_int, libc::sighandler_t)>,
}

pub fn catch_interrupts() -> Interrupts {
    #[cfg(unix)]
    {
        catching(&[libc::SIGINT], false)
    }
    #[cfg(windows)]
    {
        catching()
    }
}

/// `catch_interrupts` for every signal that ends a program at someone's request: Ctrl-C, and on Unix a request to end
/// (SIGTERM: a cancelled CI job, `timeout`) and a terminal that closed (SIGHUP). rb takes away what it made, then ends
/// as the signal would have ended it (`end_interrupted`). A signal rb was started to ignore (under nohup, say) stays
/// ignored. On Windows only Ctrl-C and Ctrl-Break are caught: a console closed, a logoff or a shutdown ends rb at once.
pub fn catch_endings() -> Interrupts {
    #[cfg(unix)]
    {
        catching(&[libc::SIGINT, libc::SIGTERM, libc::SIGHUP], true)
    }
    #[cfg(windows)]
    {
        catching()
    }
}

#[cfg(unix)]
fn catching(signals: &[libc::c_int], unless_ignored: bool) -> Interrupts {
    INTERRUPTED.store(false, std::sync::atomic::Ordering::SeqCst);
    SIGNAL.store(0, std::sync::atomic::Ordering::SeqCst);
    let mut previous = Vec::new();
    for &sig in signals {
        if unless_ignored && ignored(sig) {
            continue;
        }
        previous.push((sig, unsafe { libc::signal(sig, on_interrupt as *const () as usize) }));
    }
    Interrupts { previous }
}

/// Whether `sig` is ignored, asked without changing it, so a signal that is to be ignored is never caught even for a
/// moment.
#[cfg(unix)]
fn ignored(sig: libc::c_int) -> bool {
    let mut now: libc::sigaction = unsafe { std::mem::zeroed() };
    unsafe { libc::sigaction(sig, std::ptr::null(), &mut now) == 0 && now.sa_sigaction == libc::SIG_IGN }
}

#[cfg(windows)]
fn catching() -> Interrupts {
    INTERRUPTED.store(false, std::sync::atomic::Ordering::SeqCst);
    unsafe {
        windows_sys::Win32::System::Console::SetConsoleCtrlHandler(Some(on_interrupt), 1);
    }
    Interrupts {}
}

pub fn interrupted() -> bool {
    INTERRUPTED.load(std::sync::atomic::Ordering::SeqCst)
}

/// Whether what was noted under `catch_endings` is Ctrl-C (or nothing): not a request to end (SIGTERM) or a terminal
/// that closed (SIGHUP).
pub fn by_ctrl_c() -> bool {
    #[cfg(unix)]
    {
        matches!(SIGNAL.load(std::sync::atomic::Ordering::SeqCst), 0 | libc::SIGINT)
    }
    #[cfg(windows)]
    {
        true
    }
}

/// Pass the signal noted under `catch_endings` on to a program rb started and waits for, so it ends as rb would have.
pub fn pass_on(child: &std::process::Child) {
    #[cfg(unix)]
    {
        let sig = SIGNAL.load(std::sync::atomic::Ordering::SeqCst);
        if sig != 0 {
            unsafe {
                libc::kill(child.id() as libc::pid_t, sig);
            }
        }
    }
    #[cfg(windows)]
    let _ = child; // Ctrl-C and Ctrl-Break reach every program of the console
}

/// Where a piece of work stops once a signal that ends rb is noted under `catch_endings`, so what it made can go before
/// rb ends by that signal (`end_interrupted`). What it returns is never said.
pub fn go_on() -> crate::errors::Res<()> {
    if interrupted() {
        return Err(crate::errors::RBError::msg("E_INTERNAL", "rb was interrupted"));
    }
    Ok(())
}

/// End rb as the signal noted under `catch_endings` would have ended it, once what rb made is gone: whoever started rb
/// (a shell, a CI runner) sees it ended by that signal, as it would have been without the catch.
pub fn end_interrupted() -> ! {
    #[cfg(unix)]
    {
        let sig = match SIGNAL.load(std::sync::atomic::Ordering::SeqCst) {
            0 => libc::SIGINT,
            sig => sig,
        };
        unsafe {
            libc::signal(sig, libc::SIG_DFL);
            libc::raise(sig);
        }
        std::process::exit(128 + sig)
    }
    // the status a console program Ctrl-C ends exits with (STATUS_CONTROL_C_EXIT)
    #[cfg(windows)]
    {
        std::process::exit(0xC000_013Au32 as i32)
    }
}

impl Drop for Interrupts {
    fn drop(&mut self) {
        #[cfg(unix)]
        for &(sig, previous) in &self.previous {
            unsafe {
                libc::signal(sig, previous);
            }
        }
        #[cfg(windows)]
        unsafe {
            windows_sys::Win32::System::Console::SetConsoleCtrlHandler(Some(on_interrupt), 0);
        }
    }
}

// ---------------------------------------------------------------- a program left to run on its own

/// What was started and not yet seen to end: looked at again on the next start, so a long-running rb (rb mcp, rb
/// open) leaves no finished child behind it. Nothing here waits.
#[cfg(unix)]
static DETACHED: std::sync::Mutex<Vec<std::process::Child>> = std::sync::Mutex::new(Vec::new());

/// Start a program that runs on its own and is never waited on. It holds nothing of this process's: no standard
/// streams and no other descriptor or handle, so a caller reading this process's output to its end, or a terminal
/// another program reads, is not kept waiting for it. It starts in a neutral directory, outside the terminal's process
/// group so its Ctrl-C does not reach it. On Windows it has no console at all, so no window opens and no console Ctrl-C
/// reaches it.
#[cfg(unix)]
pub fn spawn_detached(program: &Path, args: &[&str], env: &[(&str, &str)]) -> bool {
    use std::os::unix::process::CommandExt;
    use std::process::Stdio;
    let mut cmd = std::process::Command::new(program);
    cmd.args(args).envs(env.iter().copied()).current_dir(neutral_dir()).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    cmd.process_group(0);
    // worked out before the fork: between fork and exec only async-signal-safe calls are allowed
    let end = descriptors_end();
    unsafe {
        cmd.pre_exec(move || {
            closed_on_exec_from_3(end);
            Ok(())
        });
    }
    let Ok(child) = cmd.spawn() else { return false };
    if let Ok(mut started) = DETACHED.lock() {
        started.retain_mut(|c| matches!(c.try_wait(), Ok(None)));
        started.push(child);
    }
    true
}

/// One past the highest descriptor this process has open, as /dev/fd lists them; the descriptor limit when it lists
/// nothing. A limit can be a million (a loop that long would delay the caller); the descriptors open are a few.
#[cfg(unix)]
fn descriptors_end() -> libc::c_int {
    let listed = std::fs::read_dir("/dev/fd").ok().and_then(|dir| dir.filter_map(|e| e.ok()?.file_name().to_str()?.parse::<libc::c_int>().ok()).max());
    listed.map_or_else(|| unsafe { libc::getdtablesize() }, |fd| fd + 1)
}

/// In the child between fork and exec: every descriptor from 3 up is marked close-on-exec, so the program started holds
/// none of them. Marked rather than closed: std reports a failed exec through a close-on-exec pipe of its own there.
#[cfg(unix)]
fn closed_on_exec_from_3(end: libc::c_int) {
    #[cfg(target_os = "linux")]
    if unsafe { libc::syscall(libc::SYS_close_range, 3 as libc::c_uint, libc::c_uint::MAX, libc::CLOSE_RANGE_CLOEXEC) } == 0 {
        return;
    }
    for fd in 3..end {
        unsafe { libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC) };
    }
}

#[cfg(windows)]
pub fn spawn_detached(program: &Path, args: &[&str], env: &[(&str, &str)]) -> bool {
    use std::ffi::{OsStr, OsString};
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Foundation::CloseHandle;
    use windows_sys::Win32::System::Threading::{CreateProcessW, CREATE_NEW_PROCESS_GROUP, CREATE_UNICODE_ENVIRONMENT, DETACHED_PROCESS, PROCESS_INFORMATION, STARTUPINFOW};
    let wide = |s: &OsStr| s.encode_wide().chain([0]).collect::<Vec<u16>>();
    let (application, directory) = (wide(program.as_os_str()), wide(neutral_dir().as_os_str()));
    // the program quoted as a whole (a path holds no quote), then each argument as a C runtime splits them again
    let mut line: Vec<u16> = [b'"' as u16].into_iter().chain(program.as_os_str().encode_wide()).chain([b'"' as u16]).collect();
    for arg in args {
        line.push(b' ' as u16);
        push_argument(&mut line, OsStr::new(arg));
    }
    line.push(0);
    let mut vars: Vec<(OsString, OsString)> = std::env::vars_os().filter(|(k, _)| !env.iter().any(|(name, _)| k.eq_ignore_ascii_case(name))).collect();
    vars.extend(env.iter().map(|(k, v)| (OsString::from(k), OsString::from(v))));
    // Windows asks for the block sorted by name, without regard to case
    vars.sort_by_cached_key(|(k, _)| k.to_ascii_uppercase());
    let mut block: Vec<u16> = vars.iter().flat_map(|(k, v)| k.encode_wide().chain([b'=' as u16]).chain(v.encode_wide()).chain([0])).collect();
    block.push(0);
    if vars.is_empty() {
        block.push(0); // an empty block is two NULs
    }
    let info = STARTUPINFOW { cb: std::mem::size_of::<STARTUPINFOW>() as u32, ..Default::default() };
    let mut started = PROCESS_INFORMATION::default();
    // no handle is inherited and none is named; DETACHED_PROCESS also keeps Windows from duplicating this process's
    // standard handles into a console program started without inheritance
    let made = unsafe {
        CreateProcessW(application.as_ptr(), line.as_mut_ptr(), std::ptr::null(), std::ptr::null(), 0, DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_UNICODE_ENVIRONMENT,
            block.as_ptr().cast(), directory.as_ptr(), &info, &mut started)
    } != 0;
    if made {
        unsafe {
            CloseHandle(started.hProcess);
            CloseHandle(started.hThread);
        }
    }
    made
}

/// One argument of a command line as a C runtime splits it: quoted when it is empty or holds a space, a tab or a quote,
/// with each quote escaped and the backslashes before one, or before the closing quote, doubled.
#[cfg(windows)]
fn push_argument(line: &mut Vec<u16>, arg: &std::ffi::OsStr) {
    use std::os::windows::ffi::OsStrExt;
    let (quote, backslash) = (b'"' as u16, b'\\' as u16);
    if !arg.is_empty() && !arg.encode_wide().any(|c| matches!(c, 0x20 | 0x09 | 0x0a | 0x0b) || c == quote) {
        line.extend(arg.encode_wide());
        return;
    }
    line.push(quote);
    let mut backslashes = 0;
    for c in arg.encode_wide() {
        if c == backslash {
            backslashes += 1;
        } else {
            if c == quote {
                line.extend(std::iter::repeat(backslash).take(backslashes + 1));
            }
            backslashes = 0;
        }
        line.push(c);
    }
    line.extend(std::iter::repeat(backslash).take(backslashes));
    line.push(quote);
}

// ---------------------------------------------------------------- the Python beside rb

/// The program a watched command names, as the user's shell finds it. Windows starts a bare name only as an .exe;
/// many tools there are a .cmd or .bat on PATH (pyenv's python, npm, conda), which cmd and PowerShell find through
/// PATHEXT. So does this: the first directory of PATH that has the name with .com, .exe, .bat or .cmd, in PATHEXT's
/// order. A name with a directory or an extension of its own is taken as it is.
#[cfg(windows)]
pub fn find_program(name: &str) -> std::ffi::OsString {
    let as_given = std::ffi::OsString::from(name);
    let p = Path::new(name);
    if name.contains(['/', '\\', ':']) || p.extension().is_some() {
        return as_given;
    }
    let listed = std::env::var("PATHEXT").unwrap_or_else(|_| ".COM;.EXE;.BAT;.CMD".into());
    let extensions: Vec<String> = listed.split(';').map(|e| e.trim().to_ascii_lowercase()).filter(|e| matches!(e.as_str(), ".com" | ".exe" | ".bat" | ".cmd")).collect();
    let Some(path) = std::env::var_os("PATH") else { return as_given };
    for dir in std::env::split_paths(&path) {
        for ext in &extensions {
            let candidate = dir.join(format!("{name}{ext}"));
            if candidate.is_file() {
                return candidate.into_os_string();
            }
        }
    }
    as_given
}

#[cfg(not(windows))]
pub fn find_program(name: &str) -> std::ffi::OsString {
    std::ffi::OsString::from(name)
}

/// The names a Python interpreter goes by in a directory of programs.
pub const PYTHON_NAMES: &[&str] = if cfg!(windows) { &["python.exe", "python3.exe"] } else { &["python3", "python"] };

/// The name of a program's file: `rb`, or `rb.exe` on Windows.
pub fn program(name: &str) -> String {
    format!("{name}{}", std::env::consts::EXE_SUFFIX)
}

/// Tests that start programs take turns with tests of a file lock: a starting child keeps a lock the test let go of.
#[cfg(test)]
pub(crate) mod turns {
    use std::sync::{Condvar, Mutex, MutexGuard};

    /// How many tests of a lock run, and whether a test that starts programs does.
    static RUNNING: Mutex<(usize, bool)> = Mutex::new((0, false));
    static CHANGED: Condvar = Condvar::new();

    fn running() -> MutexGuard<'static, (usize, bool)> {
        RUNNING.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    fn once(ready: impl Fn(&(usize, bool)) -> bool) -> MutexGuard<'static, (usize, bool)> {
        CHANGED.wait_while(running(), |now| !ready(now)).unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    /// A test of a lock holds it throughout; it waits only on a test that starts programs, so a test may hold two.
    pub(crate) struct Locking(());

    pub(crate) fn locking() -> Locking {
        once(|now| !now.1).0 += 1;
        Locking(())
    }

    impl Drop for Locking {
        fn drop(&mut self) {
            running().0 -= 1;
            CHANGED.notify_all();
        }
    }

    /// A test that starts programs holds it throughout; it waits until no test of a lock runs.
    pub(crate) struct Starting(());

    pub(crate) fn starting() -> Starting {
        once(|now| now.0 == 0 && !now.1).1 = true;
        Starting(())
    }

    impl Drop for Starting {
        fn drop(&mut self) {
            running().1 = false;
            CHANGED.notify_all();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_system_error_is_said_in_the_systems_words_alone() {
        let missing = std::fs::read("no-such-directory-anywhere/no-such-file").unwrap_err();
        let said = os_words(&missing);
        assert!(!said.is_empty() && !said.contains("os error") && !said.contains('[') && !said.ends_with('.'), "{said}");
        assert_eq!(said, os_code_words(missing.raw_os_error().unwrap()));
        if cfg!(unix) {
            assert_eq!(said, "No such file or directory");
        }
        // an error that is rb's own sentence stays that sentence, without a final stop to double
        assert_eq!(os_words(&std::io::Error::new(std::io::ErrorKind::InvalidInput, "con is a name Windows keeps for a device.")), "con is a name Windows keeps for a device");
    }

    #[test]
    fn a_zip_name_cannot_climb_out() {
        assert_eq!(zip_parts("a/./b/../c.yaml"), vec!["a", "b", "c.yaml"]);
        assert_eq!(zip_parts("/etc/passwd"), vec!["etc", "passwd"]);
        if cfg!(windows) {
            assert_eq!(zip_parts("..\\..\\x"), vec!["x"]);
            assert_eq!(zip_parts("C:\\Windows\\x"), vec!["C_", "Windows", "x"]);
            assert_eq!(zip_parts("a/b. /c"), vec!["a", "b", "c"]);
        } else {
            assert_eq!(zip_parts("a\\b"), vec!["a\\b"]);
        }
    }

    #[test]
    fn a_stamp_tells_a_replaced_file_from_the_one_before() {
        let dir = std::env::temp_dir().join(format!("rb-sys-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let (a, b) = (dir.join("a.txt"), dir.join("a.txt.tmp"));
        std::fs::write(&a, "one").unwrap();
        let first = file_stamp(&a).unwrap();
        assert_eq!(first.1, 3);
        std::fs::write(&b, "two").unwrap();
        replace(&b, &a).unwrap();
        let second = file_stamp(&a).unwrap();
        assert_eq!(std::fs::read_to_string(&a).unwrap(), "two");
        assert_ne!(first.2, second.2); // another file under the same name
        let _ = std::fs::remove_file(&a);
        let _ = std::fs::remove_dir(&dir);
    }

    #[test]
    fn the_canonical_path_is_one_other_programs_take() {
        let here = canonicalize(Path::new(".")).unwrap();
        assert!(here.is_absolute());
        assert!(!here.to_string_lossy().starts_with("\\\\?\\"));
    }

    #[test]
    fn a_device_name_is_known_where_it_matters() {
        let named = |p: &str| device_name(Path::new(p));
        if cfg!(windows) {
            assert_eq!(named(".rb/experiments/aux/experiment.yaml").as_deref(), Some("aux"));
            assert_eq!(named(".rb/experiments/e1/variants/nul.yaml").as_deref(), Some("nul.yaml"));
            assert_eq!(named(".rb/metrics/COM3.yaml").as_deref(), Some("COM3.yaml"));
            assert_eq!(named(".rb/experiments/auxiliary/variants/com.yaml"), None);
        } else {
            assert_eq!(named(".rb/experiments/aux/experiment.yaml"), None);
        }
    }

    #[test]
    fn the_system_gives_random_bytes() {
        let (mut a, mut b) = ([0u8; 16], [0u8; 16]);
        assert!(random_bytes(&mut a) && random_bytes(&mut b));
        assert_ne!(a, b);
    }

    #[test]
    fn a_lock_is_held_until_dropped() {
        let _turn = turns::locking();
        let p = std::env::temp_dir().join(format!("rb-sys-lock-{}", std::process::id()));
        let held = lock(&p).unwrap();
        let other = std::fs::OpenOptions::new().read(true).write(true).open(&p).unwrap();
        assert!(other.try_lock().is_err());
        drop(held);
        // a program another test starts holds a copy of the lock's file from its fork to its exec, so the release
        // shows within moments of the drop, not always at once: wait for it, up to five seconds
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        while other.try_lock().is_err() {
            assert!(std::time::Instant::now() < deadline, "the lock was still held five seconds after it was dropped");
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        drop(other);
        let _ = std::fs::remove_file(&p);
    }

    /// The signals' handlers are the whole process's: every test that changes one holds this while it does.
    #[cfg(unix)]
    static SIGNALS: std::sync::Mutex<()> = std::sync::Mutex::new(());

    /// The handlers of SIGHUP and SIGTERM as they were, put back however the test ends.
    #[cfg(unix)]
    struct Restored([(libc::c_int, libc::sighandler_t); 2]);

    #[cfg(unix)]
    impl Drop for Restored {
        fn drop(&mut self) {
            for (sig, handler) in self.0 {
                unsafe { libc::signal(sig, handler) };
            }
        }
    }

    /// While rb holds the signals that end a program, a signal it was started to ignore stays ignored, and each comes
    /// back as it was when they are let go.
    #[cfg(unix)]
    #[test]
    fn an_ignored_signal_stays_ignored_while_the_others_are_held() {
        let _alone = SIGNALS.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        let now = |sig: libc::c_int| {
            let mut act: libc::sigaction = unsafe { std::mem::zeroed() };
            unsafe { libc::sigaction(sig, std::ptr::null(), &mut act) };
            act.sa_sigaction
        };
        let term = now(libc::SIGTERM);
        let _restored = Restored([(libc::SIGHUP, unsafe { libc::signal(libc::SIGHUP, libc::SIG_IGN) }), (libc::SIGTERM, term)]);
        let held = catch_endings();
        assert!(ignored(libc::SIGHUP) && !ignored(libc::SIGTERM));
        assert_eq!(now(libc::SIGTERM), on_interrupt as *const () as usize);
        drop(held);
        assert_eq!((now(libc::SIGHUP), now(libc::SIGTERM)), (libc::SIG_IGN, term));
    }
}
