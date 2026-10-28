//! rb tour: a one-minute walkthrough. It builds a small real project in a directory of its own by running the same
//! commands someone would type, each printed before its own real output.

use super::*;

type R = Res<i32>;

const DEFAULT_DIR: &str = "rb-tour";
const RUN_CONFIG: &str = "optim:\n  lr: 0.001\ndropout: 0.1\nseed: {seed}\n";

fn files() -> Vec<(&'static str, Value)> {
    vec![
        ("configs/train.json", crate::obj! {"optim" => crate::obj! {"lr" => 0.001}}),
        ("configs/none.json", crate::obj! {"dropout" => 0.0}),
        ("configs/p01.json", crate::obj! {"dropout" => 0.1}),
    ]
}

fn outputs() -> Vec<(&'static str, Value)> {
    vec![
        ("out/s1/metrics.json", crate::obj! {"none" => crate::obj! {"val_loss" => 1.31}, "p01" => crate::obj! {"val_loss" => 1.27, "step_ms" => 20.4}}),
        ("out/s2/metrics.json", crate::obj! {"none" => crate::obj! {"val_loss" => 1.33}, "p01" => crate::obj! {"val_loss" => 1.24, "step_ms" => 20.6}}),
        ("out/s3/metrics.json", crate::obj! {"none" => crate::obj! {"val_loss" => 1.29}, "p01" => crate::obj! {"val_loss" => 1.24}}),
    ]
}

fn steps() -> Vec<Vec<&'static str>> {
    vec![
        vec!["init", "Dropout on a tiny MLP"],
        vec!["question", "add", "Can we use dropout 0.1 within our loss and step-time budgets?", "--id", "q1"],
        vec![
            "experiment", "add", "dropout 0.1 vs none", "--id", "drop", "-q", "q1", "--hypothesis", "Dropout 0.1 lowers val_loss", "-m", "less co-adaptation",
            "--baseline", "none", "--candidate", "p01", "--varies", "dropout", "--note", "assumes the validation split stayed fixed",
        ],
        vec!["spec", "set", "drop", "--from", "configs/train.json", "--keys", "optim.lr"],
        vec!["spec", "set", "drop", "none.dropout", "0.0", "--source", "configs/none.json#dropout"],
        vec!["spec", "set", "drop", "p01.dropout", "0.1", "--source", "configs/p01.json#dropout"],
        vec!["spec", "set", "drop", "seed", "--per-run"],
        vec!["metric", "add", "val_loss", "--unit", "nats", "--minimize"],
        vec!["metric", "add", "step_ms", "--unit", "ms", "--minimize"],
        vec![
            "criterion", "add", "Dropout 0.1 costs at most 0.02 val_loss", "-e", "drop", "--metric", "change.val_loss", "--at-most", "0.02", "--min-n", "3",
            "--noise", "0.01", "--id", "c1", "-q", "q1", "--when-met", "Dropout 0.1 meets the declared validation-loss budget.",
        ],
        vec!["criterion", "add", "Dropout keeps a step under 21 ms", "-e", "drop", "--metric", "p01.step_ms", "--at-most", "21", "--min-n", "3", "--id", "c2", "-q", "q1", "--when-met", "Dropout 0.1 meets the declared step-time budget."],
        vec!["freeze", "drop", "-m", "before the runs that count"],
        vec![
            "run", "add", "drop", "--from", "out/s1/metrics.json", "--set", "seed=1", "--config", "out/s1/config.yaml", "--command",
            "python train.py --dropout 0.1 --seed 1",
        ],
        vec![
            "run", "add", "drop", "--from", "out/s2/metrics.json", "--set", "seed=2", "--config", "out/s2/config.yaml", "--command",
            "python train.py --dropout 0.1 --seed 2",
        ],
        vec![
            "run", "add", "drop", "--from", "out/s3/metrics.json", "--set", "seed=3", "--config", "out/s3/config.yaml", "--command",
            "python train.py --dropout 0.1 --seed 3",
        ],
        vec!["status"],
    ]
}

/// Under RB_TEST_NOW the clock is fixed, and a file the tour writes is dated by it too (the golden-record harness).
fn stamp(p: &Path) {
    if std::env::var("RB_TEST_NOW").map_or(false, |v| !v.is_empty()) {
        let t = crate::pytime::now_dt();
        let ts = (t.timestamp(), t.timestamp_subsec_nanos());
        crate::sys::set_times(p, ts, ts);
    }
}

fn write_json_files(dest: &Path, files: &[(&str, Value)]) -> Res<()> {
    for (rel, data) in files {
        let p = dest.join(rel);
        if let Some(parent) = p.parent() {
            std::fs::create_dir_all(parent).map_err(|e| RBError::could_not_write(&e, parent))?;
        }
        let text = pyjson::dumps_with(data, &pyjson::Dumps::indent(1)).unwrap_or_default() + "\n";
        std::fs::write(&p, text).map_err(|e| RBError::could_not_write(&e, &p))?;
        stamp(&p);
    }
    Ok(())
}

/// The project before any run: its configs, committed, or staged where git has no identity set.
fn scaffold(dest: &Path) -> Res<()> {
    write_json_files(dest, &files())?;
    let git = |args: &[&str]| std::process::Command::new("git").args(args).current_dir(dest).stdin(std::process::Stdio::null()).output();
    if git(&["init", "-q"]).map_or(false, |o| o.status.success()) && git(&["add", "-A"]).map_or(false, |o| o.status.success()) {
        // never under an identity git guesses from the account and host: without one in its config the files stay staged
        let _ = git(&["-c", "user.useConfigOnly=true", "commit", "-q", "-m", "tour scaffolding"]);
    }
    crate::sources::forget_git(); // the repository is new: nothing git said of this place before holds
    Ok(())
}

/// Whether rb runs from uvx's cache of tool environments (uv's archive-v<N>), where `rb` is not on the user's PATH
/// once the tour ends. A cache uv keeps elsewhere (UV_CACHE_DIR) is not told apart.
fn from_uvx(exe: &Path) -> bool {
    let parts: Vec<String> = exe.components().map(|c| c.as_os_str().to_string_lossy().to_lowercase()).collect();
    parts.iter().enumerate().any(|(i, p)| p.starts_with("archive-v") && parts[..i].iter().any(|q| q == "uv"))
}

pub fn cmd_tour(ns: &Namespace, out: &mut Out) -> R {
    if Ledger::open(None).is_ok() {
        return Err(RBError::msg_fix(
            "E_PROJECT_EXISTS",
            "The tour starts its own project, and there is already one here.",
            "Run rb tour from a directory outside it; the tour lands in ./rb-tour.",
        ));
    }
    let json = flag(ns, "json");
    let dir = s(ns, "dir").filter(|d| !d.is_empty()).unwrap_or_else(|| DEFAULT_DIR.to_string());
    let dest = PathBuf::from(pathlib_str(&dir));
    if dest.exists() && std::fs::read_dir(&dest).map_or(false, |mut rd| rd.next().is_some()) {
        return Err(RBError::msg_fix("E_PROJECT_EXISTS", format!("{} already exists and is not empty.", crate::sys::slashed(&dest)), "Pick a fresh directory: rb tour <dir>."));
    }
    std::fs::create_dir_all(&dest).map_err(|e| RBError::could_not_write(&e, &dest))?;
    scaffold(&dest)?;
    let mut steps_v: Vec<Value> = Vec::new();
    let home = std::env::current_dir().unwrap_or_default();
    std::env::set_current_dir(&dest).map_err(|e| RBError::could_not_write(&e, &dest))?;
    // Ok(Some(code)): a step failed and said why in the terminal; the tour stops there with that step's exit code
    let result = (|| -> Res<Option<i32>> {
        let mut ran = false;
        for argv in steps() {
            if argv[0] == "run" && !ran {
                let here = std::env::current_dir().unwrap_or_default();
                write_json_files(&here, &outputs())?;
                for seed in 1..=3 {
                    let p = here.join(format!("out/s{seed}/config.yaml"));
                    std::fs::write(&p, RUN_CONFIG.replace("{seed}", &seed.to_string())).map_err(|e| RBError::could_not_write(&e, &p))?;
                    stamp(&p);
                }
                ran = true;
                if !json {
                    crate::cli::println_out("\n$ python train.py --dropout 0.1 --seed 1   (and seeds 2 and 3: the tour writes their out/ for you)");
                }
            }
            let owned: Vec<String> = argv.iter().map(|a| a.to_string()).collect();
            let line = format!("rb {}", crate::cli::shlex_join(&owned));
            if json {
                let mut with_json = owned.clone();
                with_json.push("--json".into());
                let (code, text) = crate::cli::run_capturing(with_json, false);
                let env = pyjson::loads(&text).unwrap_or_else(|_| crate::obj! {"raw" => text.clone()});
                // the step's own failure is the tour's: its code, what it said, what to do, and its exit code
                let failed = env.get("errors").and_then(Value::as_list).and_then(|l| l.first()).cloned();
                steps_v.push(crate::obj! {"command" => line.clone(), "exit" => code, "envelope" => env});
                if code != 0 {
                    let part = |k: &str| failed.as_ref().and_then(|e| e.get_str(k)).unwrap_or_default().to_string();
                    let err = match part("code").as_str() {
                        "" => RBError::fault(format!("the tour stopped at `{line}`, which exited {code} and said no more")),
                        named => RBError::msg_fix(named, format!("The tour stopped at `{line}`: {}", part("message")), part("fix")).with_exit(code),
                    };
                    return Err(err);
                }
            } else {
                crate::cli::println_out(&format!("\n$ {line}"));
                let code = crate::cli::main(owned);
                if code != 0 {
                    crate::cli::eprintln_err(&format!("rb tour: The tour stopped at: {line}"));
                    crate::cli::eprintln_err("  → What stopped it is said just above. Fix that, then run rb tour again in a fresh directory.");
                    return Ok(Some(code));
                }
            }
        }
        Ok(None)
    })();
    let _ = std::env::set_current_dir(&home);
    if let Some(code) = result? {
        return Ok(code);
    }
    // the tour's folder, in the ownership ledger of the checkout it was made in, with every file as the tour left it
    let made = crate::sources::resolve_path(&home.join(&dest));
    let fields = crate::dict! {"path" => crate::sys::slashed(&made), "how" => "dir", "kind" => "tour", "files" => Value::Dict(crate::owned::tree_sums(&made))};
    crate::owned::record(&home, crate::owned::scope_of(&home, &made), fields);
    let shown = crate::sys::slashed(&dest);
    if json {
        out.data = crate::dict! {"path" => shown.clone(), "steps" => Value::List(steps_v)};
        out.say(format!("Tour written to {shown}/: {} commands ran.", steps().len()));
    } else {
        let uvx = std::env::current_exe().is_ok_and(|exe| from_uvx(&exe));
        let closing = [
            String::new(),
            format!("That is the loop, and {shown}/{}/ is an ordinary record, YAML files you can read: poke at it.", crate::ledger::DIR),
            "One criterion meets its rule: three seeds, declared and frozen before the runs, every setting with its source.".into(),
            "The other is undetermined: two seeds report step_ms and it asks for three. rb will not round that up, and neither should anyone.".into(),
            String::new(),
            format!("Keep going, in the record:  cd {shown}"),
            "  rb ui         your screen in this terminal      rb open    the same, in your browser".into(),
            "  rb page       the record as one HTML file       rb export  the page to publish, the record if asked".into(),
            "  rb docs       the whole manual                  rb skill add   teach your coding agent the discipline".into(),
        ];
        crate::cli::println_out(&closing.join("\n"));
        if uvx {
            crate::cli::println_out("  rb is not on your PATH after uvx: start each with uvx --from rabbit-brain (uvx --from rabbit-brain rb ui), or keep rb with uv tool install rabbit-brain.");
        }
        let commands: Vec<String> = steps().iter().map(|s| format!("rb {}", crate::cli::shlex_join(&s.iter().map(|a| a.to_string()).collect::<Vec<_>>()))).collect();
        out.data = crate::dict! {"path" => shown, "commands" => Value::list_of_str(commands)};
    }
    Ok(0)
}

#[cfg(test)]
mod tests {
    use super::from_uvx;
    use std::path::Path;

    /// rb in uvx's cache is told apart from rb installed with pip, uv tool or pipx.
    #[test]
    fn rb_run_by_uvx_is_told_apart() {
        assert!(from_uvx(Path::new("/home/ana/.cache/uv/archive-v0/Xy12ab/bin/rb")));
        assert!(from_uvx(Path::new("/Users/ana/.cache/uv/archive-v0/Xy12ab/bin/rb")));
        assert!(!from_uvx(Path::new("/home/ana/.local/share/uv/tools/rabbit-brain/bin/rb")));
        assert!(!from_uvx(Path::new("/home/ana/project/.venv/bin/rb")));
        assert!(!from_uvx(Path::new("/home/ana/.local/bin/rb")));
    }
}
