//! `rabbit-brain`: the package's own name for rb (`uvx rabbit-brain ...`, `pipx run rabbit-brain ...`). It runs the
//! `rb` installed beside it with the same arguments, so there is one rb, not two.

fn main() {
    let name = format!("rb{}", std::env::consts::EXE_SUFFIX);
    let beside = std::env::current_exe().ok().and_then(|p| p.parent().map(|d| d.join(&name))).filter(|p| p.is_file());
    let Some(rb) = beside else {
        eprintln!("rabbit-brain: rb is not installed beside this command; reinstall rabbit-brain: pip install --force-reinstall \"rabbit-brain=={}\".", rb::version());
        std::process::exit(3);
    };
    let mut command = std::process::Command::new(&rb);
    command.args(std::env::args_os().skip(1));
    // Linux and macOS: this process becomes rb
    #[cfg(unix)]
    let err = {
        use std::os::unix::process::CommandExt;
        command.exec()
    };
    // Windows has no such call: rb runs as a child and its exit code is this one's; Ctrl-C reaches both, and this
    // one waits for rb to end as rb chooses to
    #[cfg(not(unix))]
    let err = {
        unsafe extern "system" fn stay(_: u32) -> i32 {
            1
        }
        #[link(name = "kernel32")]
        extern "system" {
            fn SetConsoleCtrlHandler(handler: Option<unsafe extern "system" fn(u32) -> i32>, add: i32) -> i32;
        }
        unsafe {
            SetConsoleCtrlHandler(Some(stay), 1);
        }
        match command.status() {
            Ok(status) => std::process::exit(status.code().unwrap_or(3)),
            Err(e) => e,
        }
    };
    // the system's own words for why, without its code
    let why = err.to_string();
    let why = why.split(" (os error").next().unwrap_or(&why).trim_end_matches(|c: char| c == '.' || c.is_whitespace());
    eprintln!("rabbit-brain: could not start {}: {why}", rb.display());
    std::process::exit(3);
}
