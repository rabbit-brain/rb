// The app runner's smoke: rb open on a record, its page loaded in headless Chrome, and both stopped.
//   node tests/app/smoke.mjs --rb <rb> --record <checkout> [--url <address>] [--expect <text>] [--shot <png>]
//        [--load-timeout <s>, 30] [--timeout <s>, 120]
// --record starts rb open there on a free port and loads its address, or --url instead. The page must answer 200,
// finish loading within --load-timeout, show --expect (drawn by its script from the record) and throw nothing, all
// within --timeout. Exit 0 when it does, 1 when it does not or a deadline passes, 3 when there is no Chrome. The last
// line on stdout says what happened, as JSON.
import { findChrome, launchChrome, parseArgs, run, startRbOpen } from './cdp.mjs';

const a = parseArgs(process.argv.slice(2));
if (!a.url && !(a.rb && a.record)) {
  process.stderr.write('give --rb and --record, or --url\n');
  process.exit(2);
}

await run(async () => {
  const chrome = findChrome();
  const open = a.rb && a.record ? await startRbOpen({ rb: a.rb, cwd: a.record }) : null;
  const url = a.url || open.url + '/';
  const browser = await launchChrome({ chrome });
  const page = await browser.page();
  await page.viewport(1280, 900);
  const { status } = await page.goto(url, { timeoutMs: Number(a['load-timeout'] || 30) * 1000 });
  if (status !== 200) throw new Error(`cannot load ${url}: it answered ${status}`);
  if (a.expect && !(await page.waitFor(`document.body && document.body.innerText.includes(${JSON.stringify(a.expect)})`, 30000))) {
    throw new Error(`${url} loaded but never showed ${JSON.stringify(a.expect)}`);
  }
  await page.settle();
  if (page.errors.length) throw new Error(`${url} threw: ${page.errors.join(' | ')}`);
  const shot = a.shot ? await page.shot(a.shot) : null;
  return { url, status, title: await page.ev('document.title'), chrome: browser.version, shot };
}, { timeoutMs: Number(a.timeout || 120) * 1000 });
