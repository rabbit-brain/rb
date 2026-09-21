# report-template.html

The local report viewer, built from the same source as the one on the website. One HTML file with
the script and stylesheet already inlined, and two placeholders the CLI fills in:

- `__RB_TITLE__`: the document title.
- `__RB_DATA__`: a JSON object `{"run": ..., "evidence": ...}` inside `<script id="rb-data">`.

It is a build artifact and is checked in on purpose: the wheel has to carry it, and the site that
produces it is a separate repository. To regenerate it, run `node scripts/build-viewer.mjs` in the
site repository and copy `dist-viewer/report-template.html` here.

Nothing in the file reaches the network. It is opened from `file://`, where fetching a sibling file
is blocked, so everything the page needs has to already be in it.
