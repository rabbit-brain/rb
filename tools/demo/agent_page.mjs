// agent_beat.mjs's page walk: the steps walk.mjs films rb open's page by. With `start` (a view's address, "x-e1" for
// experiment e1's view) the agent session is filmed on that view, then the page goes back to the Overview, which the
// fresh session is filmed on; without it both are filmed on the Overview, one watch from start to end.
//   pageSteps({ base, start, agentDone, done, timeoutMs, fresh }) -> walk.mjs steps
// `agentDone` and `done` are files agent_beat.mjs makes as the agent session and the whole take end; the walk marks
// "ready" once the first view is drawn and, with `start`, "overview" once the Overview is drawn again (the fresh session
// starts after it).

const ignore = "[data-live]", parts = "#nav, #main, #insp";
// a view is drawn once it is the one view shown ("on"): an address naming nothing shows the Overview, never itself
const shows = start => `!!document.getElementById('main') && (document.querySelector('#main .view.on') || {}).id === ${JSON.stringify(start || "overview")}`;

export function pageSteps({ base, start = null, agentDone, done, timeoutMs, fresh = true }) {
  if (start !== null && (typeof start !== "string" || !/^[A-Za-z0-9][\w:.-]*$/.test(start))) throw new Error(`agent_page: ${JSON.stringify(start)} is no view's address`);
  const first = start ? `${base}#${start}` : base;
  const steps = [
    { goto: base }, { waitFor: "document.readyState === 'complete'" },
    { lead: true, eval: "localStorage.setItem('rb.guide.1', 'ended')" }, { lead: true, goto: first },
    { lead: true, waitFor: shows(start) }, { lead: true, wait: 1200 },
    { mark: "ready" },
  ];
  if (start && fresh) {
    steps.push({ watch: parts, ignore, until: agentDone, timeout: timeoutMs }, { wait: 1500 },
      { goto: base }, { waitFor: shows(null) }, { wait: 1200 }, { mark: "overview" });
  }
  steps.push({ watch: parts, ignore, until: done, timeout: timeoutMs }, { wait: 1500 });
  return steps;
}
