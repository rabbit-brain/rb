//! Usage measurements at explicit interface boundaries. No research value enters an event.
//! Nested CLI work is one operation; the tour and the TUI's background reads are excluded.

use crate::telemetry::{self, Actor, Command, Event, Outcome, RecordRoute, Surface};
use crate::value::{Dict, Value};
use std::cell::Cell;
use std::time::Instant;

thread_local! {
    static DEPTH: Cell<usize> = const { Cell::new(0) };
}

pub struct Operation {
    command: Option<Command>,
    surface: Surface,
    actor: Actor,
    started: Instant,
    suppression: Option<telemetry::Suppression>,
}

impl Operation {
    pub fn start(name: &str, surface: Surface, actor: Actor, bridge: bool) -> Self {
        let nested = DEPTH.with(|n| { let old = n.get(); n.set(old + 1); old > 0 });
        // TUI reads happen on polling, layout and highlighting. Only its explicit interaction
        // operation is measured; accepted writes run the normal CLI through the same engine.
        let quiet = nested || name == "tour" || (bridge && matches!(surface, Surface::Tui));
        Self {
            command: if bridge { Command::from_bridge(name) } else { Command::from_name(name) },
            surface,
            actor,
            started: Instant::now(),
            suppression: quiet.then(telemetry::suppress),
        }
    }

    pub fn finish(&self, code: i32, initialized: bool, ready: Option<RecordRoute>, inspected: bool, context: bool) {
        if self.suppression.is_some() || !telemetry::is_active() { return; }
        let (surface, actor) = (self.surface, self.actor);
        let mut events = Vec::new();
        if let Some(command) = self.command {
            events.push(Event::OperationCompleted {
                command, surface, actor,
                outcome: Outcome::from_exit(code),
                duration_ms: self.started.elapsed().as_millis().min(u64::MAX as u128) as u64,
            });
        }
        if initialized {
            events.push(Event::ProjectInitialized { surface, actor });
        }
        if let Some(route) = ready {
            events.push(Event::RecordReady { route, surface, actor });
        }
        if inspected {
            events.push(Event::RecordInspected { surface, actor });
        }
        if context {
            events.push(Event::ContextDelivered { surface, actor });
        }
        // one queue update per operation, so commands finishing together contend less for its lock
        telemetry::capture(&events);
    }
}

impl Drop for Operation {
    fn drop(&mut self) {
        DEPTH.with(|n| n.set(n.get().saturating_sub(1)));
    }
}

/// The surface a command is used through: the user's screen for rb ui, the local web viewer for rb open.
pub fn cli_surface(command: &str) -> (Surface, Actor) {
    let (via, agent) = crate::ledger::session(None, None);
    if via == "mcp" {
        return (Surface::Mcp, Actor::Agent);
    }
    let surface = match command {
        "ui" => Surface::Tui,
        "open" => Surface::Web,
        _ if std::env::var("RB_TELEMETRY_SURFACE").as_deref() == Ok("tui") => Surface::Tui,
        _ => Surface::Cli,
    };
    (surface, if agent.is_some() { Actor::Agent } else { Actor::detect() })
}

/// Whether rb reconcile --apply recorded a run itself, by what it wrote: a `recorded` line that is not a refusal.
pub fn reconciled(data: &Dict) -> bool {
    data.get("recorded").and_then(Value::as_list).map_or(false, |lines| {
        lines.iter().any(|line| line.as_str().map_or(false, |s| !s.starts_with("not recorded")))
    })
}

pub fn inspected(surface: Surface) {
    telemetry::capture(&[Event::RecordInspected { surface, actor: Actor::detect() }]);
    telemetry::flush_if_due();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_screen_and_the_web_viewer_are_surfaces_of_their_own() {
        assert_eq!(cli_surface("ui").0, Surface::Tui);
        assert_eq!(cli_surface("open").0, Surface::Web);
    }

    #[test]
    fn readiness_by_reconcile_needs_a_run_that_apply_recorded() {
        let wrote = |lines: &[&str]| crate::dict! {"recorded" => Value::list_of_str(lines.iter().copied())};
        assert!(!reconciled(&Dict::new()));
        assert!(!reconciled(&wrote(&[])));
        assert!(!reconciled(&wrote(&["not recorded: rb run add e1 score=0.9 (No experiment 'e1' in this project.)"])));
        assert!(reconciled(&wrote(&["not recorded: rb run add e1 x=1 (refused)", "run4k2xa9: rb run add e2 score=0.91"])));
    }
}
