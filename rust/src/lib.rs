//! Rabbit Brain (rb): the record of the experiments your coding agents run, as Rabbit Brain YAML files in `.rb/`
//! beside the code. This crate is the whole core: the format, the ledger, findings, and the command line.

pub mod sys;
pub mod value;
pub mod pyfloat;
pub mod pyjson;
pub mod pytime;
pub mod errors_table;
pub mod errors;
pub mod actor;
pub mod plain;
pub mod rbyaml;
pub mod argparse;
pub mod cli;
pub mod commands;
pub mod ledger;
pub mod dry;
pub mod delta;
pub mod mcp;
pub mod bridge;
pub mod rng;
pub mod model;
pub mod record_gen;
pub mod record;
pub mod pyyaml;
pub mod sources;
pub mod csvtable;
pub mod sqlite;
pub mod environment;
pub mod assess;
pub mod repo;
pub mod journal;
pub mod surface;
pub mod page_assets;
pub mod zipfile;
pub mod export;
pub mod clone;
pub mod compiler;
pub mod webui;
pub mod http;
pub mod hub;
pub mod elsewhere;
pub mod places;
pub mod checkout;
pub mod view;
pub mod overview;
pub mod files;
pub mod plainfile;
pub mod card;
pub mod graph;
pub mod reproduce;
pub mod capture;
pub mod history;
pub mod next_step;
pub mod acquisition;
pub mod getit;
pub mod shell;
pub mod skill;
pub mod owned;
pub mod splice;
pub mod remove;
pub mod telemetry;
pub mod usage;
pub mod cmds;

pub const VERSION: &str = "0.8.6";

/// The version rb says it is: VERSION, or RB_TEST_VERSION when the tests pin it (as RB_TEST_NOW pins the clock and
/// RB_TEST_SEED the ids), so what they expect outlives a release. While they pin it, a file beside the program named
/// for it with `.test-version` after (rb.test-version) pins this program alone: a test's upgrade puts a newer rb in place
/// as a copy of the same program, where no wrapper script can run (Windows).
pub fn version() -> &'static str {
    static PINNED: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    PINNED.get_or_init(|| {
        let pinned = std::env::var("RB_TEST_VERSION").ok().filter(|v| !v.is_empty());
        let beside = || {
            let exe = std::env::current_exe().ok()?;
            let mut name = exe.file_name()?.to_os_string();
            name.push(".test-version");
            std::fs::read_to_string(exe.with_file_name(name)).ok().map(|t| t.trim().to_string()).filter(|v| !v.is_empty())
        };
        pinned.as_ref().and_then(|_| beside()).or(pinned).unwrap_or_else(|| VERSION.to_string())
    })
}
