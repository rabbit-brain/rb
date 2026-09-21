# The conformance fixtures

`comparison.ts` is a byte-for-byte copy of the website's `lib/comparison.ts`. It is not edited here.

The HTML report `rb report --open` writes runs that code; the `report.md` beside it runs
`stability.py`. Two implementations of the same rules, and a reader believes whichever file they
opened. `tests/test_conformance.py` runs both over the fixtures in `corpus.py` and fails on any
difference: a verdict, a count, a per-case label, the order of the queue, or a saved check's status
or wording.

A stale copy would prove nothing, so the copy is tied to the viewer the package actually ships:
the viewer build records a sha256 of every file it was built from in
`src/rabbit_brain/assets/report-template.sources.json`, and `tests/test_viewer.py` checks this file
against the entry for `lib/comparison.ts`. Change one without the other and the test fails.

To update, in the site repository:

    npx vite build --config vite.viewer.config.ts && node scripts/build-viewer.mjs
    cp dist-viewer/report-template.html dist-viewer/report-template.sources.json <pkg>/src/rabbit_brain/assets/
    cp lib/comparison.ts <pkg>/tests/conformance/comparison.ts

`answer.mjs` is the TypeScript side of the test. Node runs the TypeScript directly, so the file
under test is the one that ships in the viewer rather than a transpiled stand-in. It needs `zod`
resolvable from this directory or above; CI installs it, and `RB_WEB` points the test at a checkout
of the site repository instead, which has its own.
