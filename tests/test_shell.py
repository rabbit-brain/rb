"""The shell's first checks, made before the shell: the hub's inert gate on what rb export writes, and the app runner
(tests/app/), Chrome over the DevTools protocol, which drives rb open and rb page as a browser does.

- page_is_inert is the hub's gate (site-build, branch hub, web/lib/bundle-verify.ts, pageIsInert), ported with
  JavaScript's reading of each pattern, and since Oct 21 its parser gate too (hub_refusal: raw text inside SVG or
  MathML, CSS in attributes, the elements and attributes it denies). The hub serves a page only when it passes; today's export fails it, as phase 2b
  will change, and test_the_export_is_inert holds that day to a passing test.
- The browser tests need node 22 or later and Chrome (RB_CHROME, then the usual places, then google-chrome on PATH).
  Without either they skip, saying which; with RB_REQUIRE_BROWSER=1, as CI sets it, they fail instead.

The shell's renderer (BP 2a, SPRINT-OCT24.md section 1), rb page's and rb export's only one since the switch (BP 2c):
it draws the contract's document (RB_TEST_SHELL_MODEL names it in a test) as one page: the frame (top bar, nav,
footer), the Overview and Your call, and the anchors the other views fill. Each test draws the INT8 example, or a copy of it changed
the way the test needs, through rb itself.

words_problems is rb's word lint over a page: SHELL-SPEC section 12's list and the bans added since (BP 2a), with each
exemption named. Author text, verbatim in its own element (class "au"), is skipped: it is the record's, not rb's."""
import contextlib
import copy
import html
import html.parser
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

import pytest

from rbtest import AGENT_VARS, command, environment

ROOT = Path(__file__).resolve().parents[1]
BROWSER = ROOT / "tests" / "app"
REQUIRED = os.environ.get("RB_REQUIRE_BROWSER") == "1"
# rb page's views since the switch (BP 2c), each a section.view of the shell's document
PAGE_VIEWS = ["overview", "research", "criteria", "experiments", "runs", "resources", "history", "your-call", "about"]
SHELL_CARDS = ["finding-card", "run-card"]

# ---------------------------------------------------------------- the hub's inert gate

# JavaScript's \s, wider than Python's ASCII one and not Python's Unicode one; with re.ASCII, \w and \b are
# JavaScript's too, and case is folded on ASCII letters alone, as a JavaScript pattern without the u flag folds it
_S = "[\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]"


def _js(pattern: str) -> re.Pattern:
    return re.compile(pattern.replace(r"\s", _S), re.ASCII | re.IGNORECASE)


_ACTIVE = _js(r"<\s*(script|iframe|object|embed|frame\b|base\b|form\b)")
_HTTP_EQUIV = _js(r"<meta[^>]+http-equiv")
_HANDLER = _js(r"\son\w+\s*=")
_JS_URL = _js(r"javascript:")
_ICON = _js(r'<link\b[^>]*href\s*=\s*"data:image/[^"]*"[^>]*>')
_LINK = _js(r"<link\b")
_SRC = _js(r"""\bsrc\s*=\s*["']?\s*https?:""")
_ANCHOR = _js(r"<a\b[^>]*>")
_HREF = _js(r"""\bhref\s*=\s*["']?\s*https?:""")
_STYLE = _js(r"""@import|url\s*\(\s*["']?\s*https?:""")


def inert_refusals(html: str) -> list:
    """Each reason the hub's gate has to refuse `html`, in the order it checks them, with each text that trips it."""
    found = []

    def check(name: str, pattern: re.Pattern, text: str = html) -> None:
        trips = sorted({m.group(0) for m in pattern.finditer(text)})
        if trips:
            found.append(f"{name}: " + ", ".join(map(repr, trips)))
    if not html.startswith("<!doctype html>"):
        found.append("doctype: the page must start with <!doctype html>")
    check("active element", _ACTIVE)
    check("http-equiv meta", _HTTP_EQUIV)
    check("inline event handler", _HANDLER)
    check("javascript: URL", _JS_URL)
    check("loading link", _LINK, _ICON.sub("", html))
    check("external src", _SRC)
    check("absolute href outside a link", _HREF, _ANCHOR.sub("<a>", html))
    check("external style", _STYLE)
    hub = hub_refusal(html)
    if hub:
        found.append(f"the hub's gate: {hub}")
    return found


# The hub's gate since Oct 21 (lib/bundle-verify.ts pageGate; /srv/rb/x-int/handoff/hub-bundle-verify.ts) reads a page
# through htmlparser2 12 and checks its tags and attributes, never its prose. hub_refusal ports it rule for rule, with
# its own words, on Python's parser made to follow htmlparser2's stack (the elements an opening tag closes, "/>" only in
# SVG or MathML, raw text outside them). rb's patterns above stay as rb's own stricter line: an export runs nothing.
MAX_PAGE_CSS = 2 * 1024 * 1024
MAX_PAGE_TOKEN = 4 * 1024 * 1024
MAX_PAGE_DEPTH = 512
MAX_STRAY_END_TAGS = 1024
MAX_TAG_ATTRIBUTES = 256
_RAW_TEXT = ("style", "script", "title", "textarea", "xmp", "iframe", "noembed", "noframes", "noscript", "plaintext")
_SPECIAL = ("title", "textarea", "xmp", "iframe", "noembed", "noframes", "plaintext")
_DENIED = ("iframe", "object", "embed", "frame", "frameset", "base", "form", "applet", "portal", "fencedframe",
           "animate", "animatemotion", "animatetransform", "set")
_LOADING = ("ping", "srcdoc", "srcset", "imagesrcset", "background", "archive", "codebase", "manifest")
_URL_ATTRS = ("src", "href", "xlink:href", "poster", "action", "formaction", "data", "cite")
_VOID = {"area", "base", "basefont", "br", "col", "command", "embed", "frame", "hr", "img", "input", "isindex", "keygen",
         "link", "meta", "param", "source", "track", "wbr"}
_INTEGRATION = {"mi", "mo", "mn", "ms", "mtext", "annotation-xml", "foreignObject", "desc", "title"}
_FORM = {"input", "option", "optgroup", "select", "button", "datalist", "textarea"}
_IMPLIES_CLOSE = {"tr": {"tr", "th", "td"}, "th": {"th"}, "td": {"thead", "th", "td"}, "body": {"head", "link", "script"},
                  "a": {"a"}, "li": {"li"}, "p": {"p"}, "option": {"option"}, "optgroup": {"optgroup", "option"},
                  "dd": {"dd", "dt"}, "dt": {"dd", "dt"}, "rt": {"rt", "rp"}, "rp": {"rt", "rp"},
                  "tbody": {"thead", "tbody"}, "tfoot": {"thead", "tbody"},
                  **{h: {"h1", "h2", "h3", "h4", "h5", "h6", "p"} for h in ("h1", "h2", "h3", "h4", "h5", "h6")},
                  **{f: _FORM for f in ("select", "input", "output", "button", "datalist", "textarea")},
                  **{t: {"p"} for t in ("address", "article", "aside", "blockquote", "details", "div", "dl", "fieldset",
                                        "figcaption", "figure", "footer", "form", "header", "hr", "main", "nav", "ol",
                                        "pre", "section", "table", "ul")}}
_JS_SPACES = "\t\n\x0b\x0c\r \xa0                　﻿"
_CSS_ESCAPE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})" + _S + r"?|([^\r\n\f0-9a-fA-F])|\r\n|[\r\n\f])")


def _url_value(value: str) -> str:
    """urlValue: what a browser reads a URL's scheme from."""
    return re.sub(r"[\x00-\x20\x7f]", "", value).lower()


def _embedded_image(value: str) -> bool:
    return re.match(r"data:image/(?:png|gif|jpeg|webp|avif|svg\+xml)[;,]", value.strip(_JS_SPACES), re.I) is not None


def _local_resource(value: str) -> bool:
    return value.strip(_JS_SPACES).startswith("#") or _embedded_image(value)


def _urls_are_local(css: str) -> bool:
    """urlsAreLocal: one forward pass over each url(; one that does not close cleanly fails closed."""
    opener = re.compile(r"url" + _S + r"*\(", re.I)
    pos = 0
    while (m := opener.search(css, pos)):
        i = m.end()
        while i < len(css) and css[i] in " \t\n\r\f":
            i += 1
        if i < len(css) and css[i] in "\"'":
            close = css.find(css[i], i + 1)
            if close < 0:
                return False
            value, i = css[i + 1:close], close + 1
            while i < len(css) and css[i] in " \t\n\r\f":
                i += 1
            if i >= len(css) or css[i] != ")":
                return False
        else:
            close = css.find(")", i)
            if close < 0:
                return False
            value, i = css[i:close], close
        if not _local_resource(value):
            return False
        pos = i + 1
    return True


def _css_is_inert(css: str) -> bool:
    """cssIsInert: escapes decoded, comments dropped, no @import or fetching function, every url() local."""
    plain = _CSS_ESCAPE.sub(lambda m: chr(min(int(m.group(1), 16) or 0xFFFD, 0x10FFFF)) if m.group(1) else (m.group(2) or ""), css)
    plain = re.sub(r"/\*[\s\S]*?(?:\*/|$)", "", plain)
    if re.search(r"@import\b|(?:image-set|image|expression)" + _S + r"*\(", plain, re.I | re.ASCII):
        return False
    return _urls_are_local(plain)


def _js_len(text: str) -> int:
    """JavaScript's .length: UTF-16 code units, so a character past U+FFFF counts twice."""
    return len(text.encode("utf-16-le")) // 2


def hub_refusal(page: str) -> Optional[str]:
    """None when the hub's pageGate serves `page`, else the first reason it refuses it, in the hub's words. Lengths are
    JavaScript's (UTF-16 units); a duplicate attribute is read as the parser's object keeps it, the first; a token the
    parser still holds unfinished counts toward the 4 MB cap. Not ported: the guards that fail closed if htmlparser2's
    internals move (its buffers, its end-tag accounting), which no page rb writes reaches."""
    if not re.match(r"<!doctype html>", page[:15], re.I):
        return "the page must start with <!doctype html>"
    refused = []

    class Gate(html.parser.HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.open, self.ctx, self.foreign, self.unsure = [], [None], 0, False
            self.in_style, self.css, self.css_seen, self.stray = False, "", 0, 0

        def deny(self, reason):
            refused.append(reason)

        def check_css(self, text):
            if self.css_seen + _js_len(text) > MAX_PAGE_CSS:
                return self.deny("the page's styles are over 2 MB")
            self.css_seen += _js_len(text)
            if not _css_is_inert(text):
                self.deny("the page's styles load a resource")

        def pop(self, implied, in_end_tag):
            top = self.open.pop()
            if (top in ("svg", "math") or top in _INTEGRATION) and len(self.ctx) > 1:
                self.ctx.pop()
            if implied and in_end_tag and self.foreign > 0:
                self.unsure = True
            if top in ("svg", "math") and self.foreign > 0:
                self.foreign -= 1
            if top == "style" and self.in_style:
                self.check_css(self.css)
                self.css, self.in_style = "", False

        def name(self, tag):
            if tag == "foreignobject" and (self.ctx[-1] == "svg" or "foreignObject" in self.open):
                return "foreignObject"
            return "img" if tag == "image" and self.ctx[-1] is None else tag

        def handle_starttag(self, tag, attrs, closed=False):
            here, tag = self.ctx[-1], self.name(tag)
            if len(attrs) > MAX_TAG_ATTRIBUTES:
                self.deny(f"the page has a tag with over {MAX_TAG_ATTRIBUTES} attributes")
            if _js_len(self.get_starttag_text() or "") > MAX_PAGE_TOKEN:
                self.deny("the page has a tag or comment over 4 MB")
            while self.open and self.open[-1] in _IMPLIES_CLOSE.get(tag, ()):
                self.pop(True, False)
            if len(self.open) + 1 > MAX_PAGE_DEPTH:
                self.deny(f"the page nests elements over {MAX_PAGE_DEPTH} deep")
            low = tag.lower()
            if low in _DENIED:
                self.deny("the page carries a frame, embed, base, form or SVG animation")
            inside = self.foreign > 0 or self.unsure
            if inside and low in _RAW_TEXT:
                self.deny("the page carries a style, script or text field inside SVG or MathML")
            svg_or_math = low in ("svg", "math")
            if low == "style":
                self.in_style, self.css = True, ""
            values = {}
            for attr, value in attrs:
                values.setdefault(attr, value or "")      # htmlparser2's attribute object keeps the first
            for attr, value in values.items():
                if attr.startswith("on"):
                    self.deny("the page carries an inline event handler")
                if attr in _LOADING:
                    self.deny("the page carries a loading attribute")
                if low == "meta" and attr == "http-equiv":
                    self.deny("the page carries an http-equiv meta")
                if attr == "referrerpolicy" or (low == "meta" and attr == "name" and value.strip(_JS_SPACES).lower() == "referrer"):
                    self.deny("the page sets its own referrer policy")
                if attr == "style" or ((inside or svg_or_math) and "(" in value):
                    self.check_css(value)
                if attr in _URL_ATTRS:
                    url = _url_value(value)
                    if re.match(r"(javascript|vbscript):", url):
                        self.deny("the page carries an active URL")
                    if low == "script" and attr == "src":
                        self.deny("the page carries an external script")
                    elif low == "a" and attr == "href":
                        if re.match(r"[a-z][a-z0-9+.-]*:", url) and not re.match(r"(https?|mailto|tel):", url):
                            self.deny("the page carries an active link")
                    elif attr != "cite" and not _local_resource(value):
                        self.deny("the page loads an external resource")
            if low == "link" and (not _embedded_image(values.get("href", "")) or values.get("rel", "").lower() != "icon"):
                self.deny("the page carries a loading link")
            if tag in _VOID:
                return
            self.open.append(tag)
            if svg_or_math:
                self.ctx.append(tag)
                self.foreign += 1
            elif tag in _INTEGRATION:
                self.ctx.append(None)
            if closed and self.ctx[-1] is not None:
                self.pop(True, False)
            elif here is None and tag in _SPECIAL:
                self.set_cdata_mode(tag)            # raw text to the hub's parser too: no tag inside it

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs, closed=True)

        def handle_endtag(self, tag):
            tag = self.name(tag)
            if tag in _VOID or tag not in self.open:
                self.stray += 1
                if self.stray > MAX_STRAY_END_TAGS:
                    self.deny(f"the page has over {MAX_STRAY_END_TAGS} end tags that close nothing")
                return
            k = len(self.open) - 1 - self.open[::-1].index(tag)
            while len(self.open) > k + 1:
                self.pop(True, True)
            self.pop(False, True)

        def handle_data(self, data):
            if self.in_style:
                if self.css_seen + _js_len(self.css) + _js_len(data) > MAX_PAGE_CSS:
                    self.deny("the page's styles are over 2 MB")
                else:
                    self.css += data

        def handle_comment(self, data):
            if _js_len(data) > MAX_PAGE_TOKEN:
                self.deny("the page has a tag or comment over 4 MB")
            if ">" in data:
                self.deny("the page carries a comment a browser would end early")

        def unknown_decl(self, data):                # <![CDATA[...]]>, which the hub's parser reads to "]]>"
            if data.upper().startswith("CDATA[") and ">" in data:
                self.deny("the page carries a comment a browser would end early")

    gate = Gate()
    gate.feed(page)
    if _js_len(gate.rawdata) > MAX_PAGE_TOKEN:             # what the parser still holds: a tag, value or comment left open
        gate.deny("the page has a tag or comment over 4 MB")
    gate.close()
    if gate.in_style:
        gate.check_css(gate.css)
    return refused[0] if refused else None


def page_is_inert(html: str) -> Optional[str]:
    """None when the hub would serve `html`, else the first reason it refuses it, as the hub answers."""
    refusals = inert_refusals(html)
    return refusals[0] if refusals else None


CLEAN = ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
         '<link rel="icon" href="data:image/svg+xml,%3Csvg%3E%3C/svg%3E"><title>INT8 ResNet-50 on ImageNet</title>'
         '<style>main{max-width:40em}</style></head><body><main><p>INT8 reads 75.61 top-1; text may say https:// freely, and '
         'a link may go anywhere: <a href="https://www.image-net.org/">ImageNet</a>.</p></main></body></html>')


def test_the_inert_gate_refuses_what_the_hub_refuses():
    """The port refuses each thing the hub's gate refuses, reads each pattern as JavaScript does (a no-break space is a
    space, U+0085 and an accented letter are not), and passes a page that runs and loads nothing."""
    assert page_is_inert(CLEAN) is None
    refused = {
        "<!DOCTYPE html>": "doctype", "<script>1</script>": "active element", "< iframe>": "active element",
        "<FORM>": "active element", '<base href="/">': "active element", '<meta http-equiv="refresh" content="0">': "http-equiv meta",
        '<p onclick="x()">': "inline event handler", "seeds 2 and 3 onnx_top1=75.4": "inline event handler",
        "with\u00a0online=false": "inline event handler", "drops javascript: links": "javascript: URL",
        '<link rel="stylesheet" href="s.css">': "loading link", '<img src="https://example.org/a.png">': "external src",
        '<div href="https://example.org/">': "absolute href outside a link", "<style>@import 'x.css'</style>": "external style",
        '<style>p{background:url( "http://example.org/a.png")}</style>': "external style",
    }
    for text, why in refused.items():
        page = text + CLEAN[len("<!doctype html>"):] if text.startswith("<!DOCTYPE") else CLEAN.replace("</main>", text + "</main>")
        assert page_is_inert(page) and page_is_inert(page).startswith(why), (text, inert_refusals(page))
    for text in ("seeds\u0085online=false", " on\u00e9=1", "seeds\u200bonline=false", "onnx_top1 75.4"):
        assert page_is_inert(CLEAN.replace("</main>", text + "</main>")) is None, text


def test_the_port_refuses_what_the_hubs_parser_gate_refuses():
    """hub_refusal answers as the hub's pageGate does (handoff/hub-bundle-verify.ts), in its words: raw text inside SVG or
    MathML (C's SVG <title>s, which staging refused on Oct 21), CSS in a style attribute or in an SVG attribute that
    loads, and each element, attribute and link it denies; it serves what the hub serves, prose and inline script
    included, which rb's own patterns still refuse on an export."""
    assert hub_refusal(CLEAN) is None
    cases = {
        '<svg><circle r="5"><title>75.61</title></circle></svg>': "the page carries a style, script or text field inside SVG or MathML",
        "<math><title>t</title></math>": "the page carries a style, script or text field inside SVG or MathML",
        "<div><svg><g></div><title>t</title>": "the page carries a style, script or text field inside SVG or MathML",
        '<svg><rect fill="url(https://example.org/a)"/></svg>': "the page's styles load a resource",
        '<svg fill="url&#40;//example.org/x)"></svg>': "the page's styles load a resource",
        '<p style="background:image(a.png)">x</p>': "the page's styles load a resource",
        '<p style="b\\61ckground:u\\72l(//example.org/a)">x</p>': "the page's styles load a resource",
        "<style>p{background:image-set(a.png 1x)}</style>": "the page's styles load a resource",
        "<FORM></FORM>": "the page carries a frame, embed, base, form or SVG animation",
        "<svg><animate/></svg>": "the page carries a frame, embed, base, form or SVG animation",
        '<p OnClick="x()">x</p>': "the page carries an inline event handler",
        '<img srcset="a.png 1x">': "the page carries a loading attribute",
        '<a href="/" ping="/p">x</a>': "the page carries a loading attribute",
        '<meta name="referrer" content="unsafe-url">': "the page sets its own referrer policy",
        '<a href="https://example.org/" referrerpolicy="unsafe-url">x</a>': "the page sets its own referrer policy",
        '<a href="java&#9;script:x">x</a>': "the page carries an active URL",
        '<a href="data:text/html,x">x</a>': "the page carries an active link",
        '<img src="a.png">': "the page loads an external resource",
        '<link rel="stylesheet" href="data:image/png;base64,AA">': "the page carries a loading link",
        "<!-- a > b -->": "the page carries a comment a browser would end early",
    }
    for text, why in cases.items():
        assert hub_refusal(CLEAN.replace("</main>", text + "</main>")) == why, (text, hub_refusal(CLEAN.replace("</main>", text + "</main>")))
        assert any(r == f"the hub's gate: {why}" for r in inert_refusals(CLEAN.replace("</main>", text + "</main>"))), text
    assert hub_refusal("<!DOCTYPE html>" + CLEAN[len("<!doctype html>"):]) is None
    assert hub_refusal("<html></html>") == "the page must start with <!doctype html>"
    assert hub_refusal(CLEAN.replace("</main>", "<div>" * (MAX_PAGE_DEPTH + 1) + "</main>")) == f"the page nests elements over {MAX_PAGE_DEPTH} deep"
    assert hub_refusal(CLEAN.replace("</main>", "</b>" * (MAX_STRAY_END_TAGS + 1) + "</main>")) == \
        f"the page has over {MAX_STRAY_END_TAGS} end tags that close nothing"
    # lengths as JavaScript counts them, a token left open, the first of two attributes, ASCII word boundaries
    assert hub_refusal("<!doctype html><div a=" + "a" * (MAX_PAGE_TOKEN + 1)) == "the page has a tag or comment over 4 MB"
    assert hub_refusal("<!doctype html><style>" + "\U0001f600" * (MAX_PAGE_CSS // 2 + 1) + "</style>") == "the page's styles are over 2 MB"
    assert hub_refusal('<!doctype html><p style="color:red" style="background:url(https://example.org/a)">x</p>') is None
    assert hub_refusal('<!doctype html><p style="background:url(https://example.org/a)" style="color:red">x</p>') == "the page's styles load a resource"
    assert hub_refusal("<!doctype html><style>@import\u00e9;</style>") == "the page's styles load a resource"
    # what it serves: prose, inline script, an SVG with its words beside it, a local url(), a mailto link
    for text in ("seeds 2 and 3 onnx_top1=75.4, javascript: links", "<script>const s = 1 > 0;</script>",
                 '<svg role="img" aria-label="at least -0.01"><rect fill="url(#g)"/><text>at least -0.01</text></svg>',
                 '<svg><p><p></p></svg><title>t</title>', '<a href="mailto:ana@example.org">ana</a>', '<p style="color:red">x</p>'):
        assert hub_refusal(CLEAN.replace("</main>", text + "</main>")) is None, text


def _export(demo, tmp_path: Path) -> str:
    """page.html as rb export writes it for the demo, outside the record; rb failing is an error, not the gate's answer."""
    out = tmp_path / "export"
    r = subprocess.run([*command(), "export", "--out", str(out), "--json"], cwd=demo.root, env=demo.env, capture_output=True,
                       encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    if r.returncode != 0 or not json.loads(r.stdout or "{}").get("ok"):
        raise RuntimeError(f"rb export failed ({r.returncode}): {r.stdout} {r.stderr}")
    return (out / "page.html").read_text(encoding="utf-8")


def test_the_export_is_inert(int8_demo, tmp_path):
    """The page rb export writes for the INT8 demo is one the hub serves: it runs nothing and loads nothing."""
    refusals = inert_refusals(_export(int8_demo, tmp_path))
    assert not refusals, "the hub refuses it: " + "; ".join(refusals)


# ---------------------------------------------------------------- the app runner

def _without(why: str) -> None:
    """What a browser test needs is missing: it skips, saying which, unless RB_REQUIRE_BROWSER=1 makes that a failure."""
    if REQUIRED:
        pytest.fail(f"{why}, and RB_REQUIRE_BROWSER=1 requires it", pytrace=False)
    pytest.skip(why)


def _node() -> str:
    node = shutil.which("node")
    if not node:
        _without("the app runner needs node 22 or later, and no node is on PATH")
    said = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
    m = re.fullmatch(r"v(\d+)\.\d+\.\d+\S*", said)
    if not m or int(m[1]) < 22:
        _without(f"the app runner needs node 22 or later, for its global WebSocket; {node} is {said or 'unreadable'}")
    return node


@contextlib.contextmanager
def _one_browser_at_a_time():
    """Chrome is the heaviest thing the suite starts: one at a time across test processes. The lock is the system's,
    released when its holder ends however it ends."""
    with open(Path(tempfile.gettempdir()) / "rb-tests-browser.lock", "a+b") as f:
        if os.name == "nt":
            import msvcrt
            while True:
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)     # it retries for 10 s, then raises
                    break
                except OSError:
                    continue
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _stop_group(proc: subprocess.Popen) -> None:
    """The runner and everything it started, stopped: its process group on Linux and macOS, its tree on Windows."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def _running(pid: int) -> bool:
    """Whether the process `pid` is running: as tasklist lists it on Windows, which has no harmless signal to ask with."""
    if os.name == "nt":
        listed = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"], capture_output=True, text=True).stdout
        return f'"{pid}"' in listed
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def _ended(pid: int, within: float = 0) -> bool:
    """Whether the process `pid` is not running, now or within `within` seconds."""
    until = time.monotonic() + within
    while _running(pid):
        if time.monotonic() >= until:
            return False
        time.sleep(0.1)
    return True


def _left_running(proc: subprocess.Popen) -> bool:
    """Whether anything the runner started, Chrome's helpers included, is still running 10 s after it ended: its process
    group is not empty. Windows keeps no such group to ask (cdp.mjs ends Chrome's tree there itself, and _gone looks
    for Chrome and rb open by their ids and for Chrome's helpers by its profile)."""
    if os.name == "nt":
        return False
    until = time.monotonic() + 10
    while True:
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            pass
        if time.monotonic() > until:
            return True
        time.sleep(0.1)


def _runner(node: str, script: str, *argv: str, env: dict, timeout: int = 180) -> tuple:
    """`node tests/app/<script> <argv>`: its exit code, what its last line says and its stderr. It runs in a process
    group of its own: when it ends, nothing it started may be left running, and past `timeout` it is stopped with all it
    started. A runner that finds no Chrome skips the test, or fails it under RB_REQUIRE_BROWSER=1."""
    own = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    with _one_browser_at_a_time():
        proc = subprocess.Popen([node, str(BROWSER / script), *argv], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, encoding="utf-8", errors="replace", **own)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _stop_group(proc)
            out, err = proc.communicate()
            pytest.fail(f"{script} did not end in {timeout} s: {err[-2000:]}")
        except BaseException:
            # a Ctrl-C reaches this process only, not the runner's own session: it is ended here, before the lock frees
            _stop_group(proc)
            _left_running(proc)
            raise
        left = _left_running(proc)
        _stop_group(proc)
    assert not left, f"{script} ended and left what it started running: {err[-2000:]}"
    if proc.returncode == 3:
        _without(err.strip().splitlines()[-1] if err.strip() else "no Chrome found")
    lines = out.strip().splitlines()
    try:
        said = json.loads(lines[-1])
    except (IndexError, ValueError):
        raise AssertionError(f"{script} ended {proc.returncode} without saying how: {out[-2000:]} {err[-2000:]}") from None
    return proc.returncode, said, err


def _holding(name: str) -> list:
    """The command line of each process running whose command line holds `name`: as ps lists them on Linux and macOS,
    and as PowerShell lists every process's CommandLine on Windows."""
    if os.name == "nt":
        argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-CimInstance Win32_Process | ForEach-Object { $_.CommandLine }"]
    else:
        argv = ["ps", "-A", "-ww", "-o", "command="]
    listed = subprocess.run(argv, capture_output=True, encoding="utf-8", errors="replace", timeout=120, check=True).stdout
    return [line.strip() for line in listed.splitlines() if name in line]


def _gone(said: dict, within: float = 10) -> None:
    """Nothing the runner started is left: rb open's port refuses a connection, Chrome and rb open have ended, within
    `within` seconds no process is left that was started with Chrome's profile, as each of its helpers is, and the
    folder made for Chrome is gone."""
    started = said["started"]
    if started["rb_open_url"]:
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", int(started["rb_open_url"].rsplit(":", 1)[1])), timeout=5).close()
    for pid in (started["chrome_pid"], started["rb_open_pid"]):
        assert not (pid and _running(pid)), (pid, started)
    if started["chrome_profile"]:
        # by the name of the folder made for it, which no other process holds: a path above it may be written another way
        # (short on Windows, through /private on macOS)
        name, until = Path(started["chrome_profile"]).parent.name, time.monotonic() + within
        while (left := _holding(name)) and time.monotonic() < until:
            time.sleep(0.2)
        assert not left, (started, left)
        assert not Path(started["chrome_profile"]).parent.exists(), started


def _title(demo) -> str:
    """The record's title, which rb open's script draws from the record once it has read it."""
    return demo.rb("status")["project"]["title"]


@pytest.mark.xfail(os.name == "nt", reason="the harness's Chrome does not start on Windows (issue #1)", strict=False)
def test_the_app_runner_starts(int8_demo, tmp_path):
    """The app runner starts rb open on the INT8 demo on a free port, loads its page in headless Chrome, sees the record
    drawn with nothing thrown, takes a screenshot and stops both; at a page that cannot be loaded, it fails, and stops
    both too."""
    node = _node()
    rb, shot = command()[0], tmp_path / "smoke.png"
    code, said, err = _runner(node, "smoke.mjs", "--rb", rb, "--record", str(int8_demo.root), "--expect", _title(int8_demo), "--shot", str(shot),
                              env=int8_demo.env)
    assert code == 0 and said["ok"], err
    assert said["status"] == 200 and said["url"] == said["started"]["rb_open_url"] + "/" and said["started"]["chrome_profile"]
    assert shot.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and said["shot"]["width"] == 1280
    _gone(said)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed = s.getsockname()[1]            # free, and nothing listens on it once this closes
    url = f"http://127.0.0.1:{closed}/"
    code, said, err = _runner(node, "smoke.mjs", "--rb", rb, "--record", str(int8_demo.root), "--url", url, env=int8_demo.env)
    assert code == 1 and not said["ok"] and said["error"].startswith(f"cannot load {url}: net::ERR_CONNECTION_REFUSED"), (code, said, err)
    assert said["started"]["chrome_pid"] and said["started"]["chrome_profile"] and said["started"]["rb_open_url"]
    _gone(said)


@pytest.mark.xfail(os.name == "nt", reason="the harness's Chrome does not start on Windows (issue #1)", strict=False)
def test_the_app_runner_stops_what_it_started_at_its_deadlines(int8_demo):
    """A page that never answers holds the runner to its deadlines: the page's own to load, then the whole run's. It
    fails at whichever comes first, and rb open and Chrome end with it."""
    node = _node()
    with socket.socket() as silent:            # takes the connection, never answers
        silent.bind(("127.0.0.1", 0))
        silent.listen(8)
        url = f"http://127.0.0.1:{silent.getsockname()[1]}/"
        for deadlines, error in ((("--load-timeout", "5", "--timeout", "60"), f"{url} did not finish loading in 5 s"),
                                 (("--timeout", "15"), "stopped after 15 s")):
            code, said, err = _runner(node, "smoke.mjs", "--rb", command()[0], "--record", str(int8_demo.root), "--url", url, *deadlines,
                                      env=int8_demo.env)
            assert code == 1 and said["error"] == error, (code, said, err)
            assert said["started"]["chrome_pid"] and said["started"]["chrome_profile"] and said["started"]["rb_open_url"]
            _gone(said)


def test_an_interrupted_runner_ends_with_all_it_started_before_the_lock_frees(tmp_path, monkeypatch):
    """A Ctrl-C reaches pytest, never the runner's own session: the runner and what it started (here a node that runs
    until stopped, as Chrome does) are ended before the interrupt goes on, so no second Chrome can start beside them."""
    node = _node()
    script, pids = tmp_path / "holds.mjs", tmp_path / "pids.json"
    script.write_text("import { spawn } from 'node:child_process';\nimport { writeFileSync } from 'node:fs';\n"
                      "const held = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });\n"
                      f"writeFileSync({json.dumps(str(pids))}, JSON.stringify([process.pid, held.pid]));\n"
                      "setInterval(() => {}, 1000);\n", encoding="utf-8")
    communicate = subprocess.Popen.communicate

    def interrupted(self, *args, **kwargs):
        monkeypatch.setattr(subprocess.Popen, "communicate", communicate)
        until = time.monotonic() + 60
        while not (pids.exists() and pids.read_text(encoding="utf-8")) and time.monotonic() < until:
            time.sleep(0.05)
        raise KeyboardInterrupt
    monkeypatch.setattr(subprocess.Popen, "communicate", interrupted)
    try:
        with pytest.raises(KeyboardInterrupt):
            _runner(node, str(script), env=dict(os.environ))
        runner, held = json.loads(pids.read_text(encoding="utf-8"))
        # gone already, where the process group was waited on; Windows is given the moment taskkill takes
        within = 5 if os.name == "nt" else 0
        assert _ended(runner, within) and _ended(held, within), (runner, held)
    finally:
        if pids.exists():
            for pid in json.loads(pids.read_text(encoding="utf-8")):
                if _running(pid):
                    os.kill(pid, signal.SIGKILL if os.name != "nt" else signal.SIGTERM)


def _node_says(node: str, code: str, env: Optional[dict] = None):
    """What `code`, an ES module importing cdp.mjs's exports as `cdp`, prints on its last line, as JSON."""
    head = f"import * as cdp from {json.dumps((BROWSER / 'cdp.mjs').as_uri())};\n"
    r = subprocess.run([node, "--input-type=module", "-e", head + code], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_on_windows_the_runner_ends_chromes_whole_tree():
    """Windows ends only the process it is asked to end, so Chrome's helpers would outlive Chrome: cdp.mjs ends its whole
    tree with taskkill /T while Chrome runs. Elsewhere the process group the tests stop holds them."""
    said = _node_says(_node(), "const calls = [], run = (cmd, args) => calls.push([cmd, ...args]);\n"
                               "const ended = ['win32', 'darwin', 'linux'].map(platform => cdp.killTree(4321, { platform, run }));\n"
                               "ended.push(cdp.killTree(null, { platform: 'win32', run }));\n"
                               "console.log(JSON.stringify({ ended, calls }));")
    assert said == {"ended": [True, False, False, False], "calls": [["taskkill", "/T", "/F", "/PID", "4321"]]}


def _cdp_script(path: Path, body: str) -> str:
    """`body`, an ES module importing cdp.mjs's exports as `cdp`, written at `path` for _runner."""
    path.write_text(f"import * as cdp from {json.dumps((BROWSER / 'cdp.mjs').as_uri())};\n"
                    "import { appendFileSync, existsSync, writeFileSync } from 'node:fs';\n"
                    "const alive = pid => { try { process.kill(pid, 0); return true; } catch { return false; } };\n" + body, encoding="utf-8")
    return str(path)


def test_chromes_stopper_ends_its_tree_while_chrome_runs_before_closing_it(tmp_path):
    """Chrome's stopper asks taskkill for Chrome's whole tree on Windows while Chrome still runs and before Chrome is
    asked to close, since closed first it would leave its helpers behind. Here a real Chrome is told it runs on Windows,
    and the call is recorded, not made, so this runs on every system."""
    script = _cdp_script(tmp_path / "stopper.mjs", """
const calls = [];
let browser;
const run = (cmd, args) => calls.push({ cmd, args, alive: alive(browser.proc.pid), closing: browser.asked('Browser.close') });
await cdp.run(async () => {
  browser = await cdp.launchChrome({ tree: { platform: 'win32', run } });
  return { calls };
}, { timeoutMs: 120000 });
""")
    code, said, err = _runner(_node(), script, env=dict(os.environ))
    assert code == 0 and said["ok"], err
    pid = said["started"]["chrome_pid"]
    assert said["calls"] == [{"cmd": "taskkill", "args": ["/T", "/F", "/PID", str(pid)], "alive": True, "closing": False}], said
    _gone(said)


def test_nodes_exit_ends_chromes_tree_when_nothing_stopped_it(tmp_path):
    """When node ends with Chrome still running (an exit no stopper ran before), its exit handler asks taskkill for
    Chrome's whole tree on Windows, while Chrome runs. Recorded as above, from the exit handler, on every system."""
    log = tmp_path / "calls.jsonl"
    script = _cdp_script(tmp_path / "exits.mjs", f"""
const run = (cmd, args) => appendFileSync({json.dumps(str(log))},
  JSON.stringify({{ cmd, args, alive: alive(Number(args[args.length - 1])) }}) + '\\n');
await cdp.launchChrome({{ tree: {{ platform: 'win32', run }} }});
console.log(JSON.stringify({{ ok: true, started: cdp.started }}));
process.exit(0);
""")
    code, said, err = _runner(_node(), script, env=dict(os.environ))
    assert code == 0 and said["ok"], err
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    assert calls == [{"cmd": "taskkill", "args": ["/T", "/F", "/PID", str(said["started"]["chrome_pid"])], "alive": True}], (calls, err)
    _gone(said)


@pytest.mark.parametrize("end", ["stopped", "exited"])
def test_chromes_folder_is_removed_once_nothing_started_with_it_runs(tmp_path, end):
    """A helper of Chrome can write into its profile as it ends, after Chrome itself (its cache's index), so Chrome's
    folder is removed only once no process started with it runs: by Chrome's stopper, and by node's exit handler when
    nothing stopped it. Here a stand-in helper, started with the folder in its command line, writes there 3 s on."""
    finish = "return {};" if end == "stopped" else "console.log(JSON.stringify({ ok: true, started: cdp.started })); process.exit(0);"
    script = _cdp_script(tmp_path / "late.mjs", f"""
import {{ spawn }} from 'node:child_process';
import {{ dirname }} from 'node:path';
await cdp.run(async () => {{
  await cdp.launchChrome();
  const late = "setTimeout(() => require('fs').mkdirSync(process.argv[1] + '/profile/Default/Cache', {{ recursive: true }}), 3000)";
  spawn(process.execPath, ['-e', late, dirname(cdp.started.chrome_profile)], {{ stdio: 'ignore' }});
  {finish}
}}, {{ timeoutMs: 120000 }});
""")
    code, said, err = _runner(_node(), script, env=dict(os.environ))
    assert code == 0 and said["ok"], err
    assert not Path(said["started"]["chrome_profile"]).parent.exists(), said
    _gone(said)


def test_the_end_check_fails_on_a_chrome_folder_left_behind(tmp_path):
    """_gone fails while the folder made for Chrome is still there, though nothing holds it."""
    profile = tmp_path / "rb-chrome-left00" / "profile" / "Default" / "Cache"
    profile.mkdir(parents=True)
    started = {"chrome_pid": None, "chrome_profile": str(profile.parents[1]), "rb_open_pid": None, "rb_open_url": None}
    with pytest.raises(AssertionError):
        _gone({"started": started}, within=0)
    shutil.rmtree(tmp_path / "rb-chrome-left00")
    _gone({"started": started}, within=0)


def test_the_tests_see_chromes_helpers_while_it_runs(tmp_path):
    """While Chrome runs with a tab open, more processes than Chrome itself hold its profile in their command lines, and
    _gone fails on them even when not given Chrome's own id; once the runner has ended, none is left."""
    started, go = tmp_path / "started.json", tmp_path / "go"
    script = _cdp_script(tmp_path / "holds.mjs", f"""
await cdp.run(async () => {{
  await (await cdp.launchChrome()).page();
  writeFileSync({json.dumps(str(started))}, JSON.stringify(cdp.started));
  while (!existsSync({json.dumps(str(go))})) await cdp.sleep(50);
  return {{}};
}}, {{ timeoutMs: 180000 }});
""")
    node, ran = _node(), {}

    def runner() -> None:
        try:
            ran["said"] = _runner(node, script, env=dict(os.environ))
        except BaseException as e:      # pytest's skip and fail too, raised again below
            ran["error"] = e
    t = threading.Thread(target=runner)
    t.start()
    try:
        # the runner first waits its turn for the one browser the tests share, which another test may hold for minutes
        # on a loaded machine: wait while it lives, for as long as its own lock and deadline can take
        until = time.monotonic() + 900
        while not (started.exists() and started.read_text(encoding="utf-8")) and t.is_alive() and time.monotonic() < until:
            time.sleep(0.1)
        if "error" in ran:
            raise ran["error"]
        assert started.exists() and started.read_text(encoding="utf-8"), f"the runner never said Chrome started: {ran.get('said')}"
        running = json.loads(started.read_text(encoding="utf-8"))
        assert running["chrome_pid"] and running["chrome_profile"], running
        held = _holding(Path(running["chrome_profile"]).parent.name)
        assert len(held) >= 2, held
        with pytest.raises(AssertionError):
            _gone({"started": {**running, "chrome_pid": None, "rb_open_pid": None, "rb_open_url": None}}, within=0)
    finally:
        go.touch()
        t.join(240)
    if "error" in ran:
        raise ran["error"]
    code, said, err = ran["said"]
    assert code == 0 and said["ok"], err
    _gone(said)


def test_chrome_is_looked_for_in_the_accounts_own_applications_folder(tmp_path):
    """A per-user Chrome on macOS sits in ~/Applications. The tests give the runner the record's HOME, so the account's
    own home folder, as the system has it, is searched too, after /Applications and beside HOME's; an account with no
    passwd entry has none, and HOME's is searched alone."""
    pwd = pytest.importorskip("pwd")
    home = tmp_path / "home"
    home.mkdir()
    paths = _node_says(_node(), "console.log(JSON.stringify(cdp.usualPaths('darwin')));", env={**os.environ, "HOME": str(home)})
    app = "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    assert paths[0] == f"/{app}" and f"{home}/{app}" in paths, paths
    try:
        own = pwd.getpwuid(os.getuid()).pw_dir
    except KeyError:
        # an account the system has no passwd entry for (a container's uid): it has no own home folder to search, and
        # the runner falls back to HOME's alone
        own = None
    if own is not None:
        assert f"{own}/{app}" in paths, paths


def _png_size(path: Path) -> tuple:
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


@pytest.mark.xfail(os.name == "nt", reason="the harness's Chrome does not start on Windows (issue #1)", strict=False)
def test_the_screenshot_runner_shoots_every_view(int8_demo, tmp_path):
    """Every view of rb open and of rb page on the INT8 demo is shot, rb page printed and with its script off too, once
    more with the shell's three-pane class set on its document, and the manifest lists each file as it is."""
    node = _node()
    out = tmp_path / "shots"
    code, said, err = _runner(node, "shoot.mjs", "--rb", command()[0], "--record", str(int8_demo.root), "--out", str(out), "--widths", "390",
                              "--schemes", "dark", "--html-app", "--expect", _title(int8_demo), env=int8_demo.env, timeout=900)
    assert code == 0 and said["ok"], err
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    # rb open and rb page draw the shell's views (BP 2c, 4b), Your call among them; rb open's inspector holds a finding's
    # card and a run's
    assert (m["open"]["views"], m["open"]["cards"], m["page_views"]["tabs"], m["misses"], m["errors"]) == (PAGE_VIEWS, SHELL_CARDS, PAGE_VIEWS, [], [])
    want = [("open", v, "script", False) for v in [*PAGE_VIEWS, *SHELL_CARDS]]
    want += [("page", v, "script", False) for v in PAGE_VIEWS] + [("page", "print", "print", False)] * 2      # a PNG and the PDF
    want += [("page", v, "no-script", app) for app in (False, True) for v in ["document", *PAGE_VIEWS]]
    assert sorted((s["surface"], s["view"], s["mode"], s["html_app"]) for s in m["shots"]) == sorted(want)
    for s in m["shots"]:
        if s["file"].endswith(".pdf"):
            assert (out / s["file"]).read_bytes()[:5] == b"%PDF-", s
            continue
        assert s["width"] == 390 and s["scheme"] == "dark" and _png_size(out / s["file"]) == (s["image"]["width"], s["image"]["height"]), s
        if s["mode"] == "no-script":
            assert ("app" in s["html_class"].split()) == s["html_app"], s
    assert sorted(p.name for p in out.iterdir()) == sorted([s["file"] for s in m["shots"]] + ["manifest.json", "page.html"])


# the browser step's run line: this file, and A's app harness beside it once the workflow runs both
RUNNER_STEPS = ("python -m pytest -q -n auto tests/test_shell.py", "python -m pytest -q -n auto tests/test_shell.py tests/app/")


def test_ci_runs_the_app_runner_on_every_system_and_never_skips_it():
    """Each job in tests.yml sets up node 22, then runs this file with RB_REQUIRE_BROWSER=1 in a step with no condition,
    so on Linux, macOS and Windows a missing node or Chrome fails the run instead of skipping the app runner."""
    flow = ROOT / ".github" / "workflows" / "tests.yml"
    if not flow.is_file():
        pytest.skip("the workflows are not beside the tests")
    yaml = pytest.importorskip("yaml")
    systems = set()
    for name, job in yaml.load(flow.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)["jobs"].items():
        steps = job["steps"]
        node = [i for i, s in enumerate(steps) if s.get("uses", "").startswith("actions/setup-node@")]
        runs = [i for i, s in enumerate(steps) if s.get("env", {}).get("RB_REQUIRE_BROWSER") == "1"]
        assert len(node) == 1 and len(runs) == 1 and node[0] < runs[0], name
        assert int(steps[node[0]]["with"]["node-version"].split(".")[0]) >= 22, name
        step = steps[runs[0]]
        assert set(step) == {"name", "env", "run"} and step["run"] in RUNNER_STEPS, (name, step)
        assert "continue-on-error" not in job and "if" not in job, name
        runs_on = job["runs-on"]
        systems |= set(job["strategy"]["matrix"]["os"]) if runs_on == "${{ matrix.os }}" else {runs_on}
    assert {s.split("-")[0] for s in systems} == {"ubuntu", "macos", "windows"}


FIX = Path(__file__).parent / "fixtures" / "shell"
PAGE_MARK = "made with rb page · a snapshot of the record at "
HONEST = "A decision records a call; it does not change a reading."


def example(name: str = "owner") -> dict:
    return json.loads((FIX / f"int8-demo.{name}.view.json").read_text(encoding="utf-8"))


def rb_env(**more) -> dict:
    env = {k: v for k, v in environment().items() if k not in AGENT_VARS and not k.startswith("RB_TEST_SHELL")}
    env.update(RB_TELEMETRY="0", DO_NOT_TRACK="1", RB_TEST_NOW="2026-10-19T09:41:00.000+00:00", **more)
    return env


def rb(root: Path, *argv: str, **env) -> subprocess.CompletedProcess:
    return subprocess.run([*command(), *argv], cwd=root, env=rb_env(**env), capture_output=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL)


@pytest.fixture(scope="module")
def record(tmp_path_factory):
    root = tmp_path_factory.mktemp("shell")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    r = rb(root, "init", "INT8 ResNet-50 on ImageNet")
    assert r.returncode == 0, r.stderr
    return root


_n = iter(range(10 ** 6))


def drawn(root: Path, doc=None, surface: str = "page", raw: str = None, **env) -> tuple:
    """(page, error) of rb page or rb export under the shell's hook, drawing `doc` (the Owner example when None)."""
    n = next(_n)
    model = root / f"doc-{n}.json"
    model.write_text(raw if raw is not None else json.dumps(example() if doc is None else doc), encoding="utf-8")
    env = {"RB_TEST_SHELL": "1", "RB_TEST_SHELL_MODEL": str(model), **env}
    if surface == "page":
        out = root / f"page-{n}.html"
        r = rb(root, "page", "--out", str(out), **env)
        return (out.read_text(encoding="utf-8"), "") if r.returncode == 0 else ("", r.stderr + r.stdout)
    out = root / f"export-{n}"
    r = rb(root, "export", "--out", str(out), **env)
    return ((out / "page.html").read_text(encoding="utf-8"), "") if r.returncode == 0 else ("", r.stderr + r.stdout)


def page_of(root: Path, doc=None, surface: str = "page") -> str:
    page, error = drawn(root, doc, surface)
    assert page, error
    return page


class Html(html.parser.HTMLParser):
    """The page as elements: tag, attributes, children and text, as `viewer` reads it: on the export, a wording marked
    for other viewers (data-vw, OWNER-READER) is left out, as shell.css hides it. "none" is the export as it is opened
    from disk, with no viewer named; "owner" and "reader" are the hub's."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self, text: str, viewer: str = "none"):
        super().__init__(convert_charrefs=True)
        self.viewer = viewer
        self.root = {"tag": "#root", "attrs": {}, "kids": [], "up": None}
        self.at = self.root
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        el = {"tag": tag, "attrs": {k: v or "" for k, v in attrs}, "kids": [], "up": self.at}
        vw = el["attrs"].get("data-vw")
        if vw is None or self.viewer in vw.split():
            self.at["kids"].append(el)
        if tag not in self.VOID:
            self.at = el

    def handle_endtag(self, tag):
        el = self.at
        while el is not self.root and el["tag"] != tag:
            el = el["up"]
        if el is not self.root:
            self.at = el["up"]

    def handle_data(self, data):
        self.at["kids"].append(data)

    def all(self, el=None):
        for k in (el or self.root)["kids"]:
            if isinstance(k, dict):
                yield k
                yield from self.all(k)

    def text(self, el) -> str:
        return " ".join(" ".join(k.split()) if isinstance(k, str) else self.text(k) for k in el["kids"]).strip()

    def raw(self, el) -> str:
        """The text as a reader sees it: the pieces as they are, with no space added between elements."""
        return "".join(k if isinstance(k, str) else self.raw(k) for k in el["kids"])

    INLINE = {"a", "abbr", "b", "bdi", "cite", "code", "em", "i", "kbd", "mark", "q", "s", "samp", "small", "span", "strong", "sub", "sup", "time", "u", "var", "wbr"}

    def reading(self, el) -> str:
        """The text as a reader sees it: a block element apart from what is beside it, an inline one (a link, a span)
        joined to its words as the page writes them."""
        def walk(e) -> str:
            parts = []
            for k in e["kids"]:
                if isinstance(k, str):
                    parts.append(k)
                elif k["tag"] in self.INLINE:
                    parts.append(walk(k))
                else:
                    parts.append(" " + walk(k) + " ")
            return "".join(parts)
        return " ".join(walk(el).split())

    def by_id(self, ident: str):
        return next((e for e in self.all() if e["attrs"].get("id") == ident), None)

    def has(self, el, cls: str) -> bool:
        return cls in el["attrs"].get("class", "").split()

    def of_class(self, cls: str, within=None) -> list:
        return [e for e in self.all(within) if self.has(e, cls)]

    def inside(self, el, ancestor) -> bool:
        while el is not None:
            if el is ancestor:
                return True
            el = el["up"]
        return False


def said(text: str) -> str:
    return " ".join(text.split())


def view_text(page: str, ident: str) -> str:
    t = Html(page)
    el = t.by_id(ident)
    assert el is not None, f"no #{ident}"
    return said(t.reading(el))


# ---------------------------------------------------------------- rb's word lint

# Phase 0's word list (LINT-P0.md, its lintwords.py as p0-fix-2 left it), in its ids: SHELL-SPEC section 12's "Never
# on a screen", the writer rule and U+2014, the bans BP 1.2 and 2a added, the siblings the notes name and the agent
# commands. Each is a pattern over rb's words on a page, matched without regard to case.
BANNED = [
    ("B01", "supported", r"\bsupported\b"),
    ("B02", "contradicted", r"\bcontradict"),
    ("B03", "refuted", r"\brefut"),
    ("B04", "established", r"\bestablish"),
    ("B05", "claim", r"\bclaim"),
    ("B06", "the person", r"\bthe person\b"),
    ("B07", "a person", r"\ba person\b"),
    ("B08", "Evidence clear", r"evidence clear"),
    ("B09", "Criterion missed", r"criterion missed"),
    ("B10", "Needs your call", r"needs your call"),
    ("B11", "executed", r"\bexecuted\b"),
    ("B12", "spec frozen", r"spec frozen"),
    ("B13", "met on average", r"met on average"),
    ("B14", "Evidence inconclusive", r"evidence inconclusive"),
    ("B15", "Integrity ok", r"integrity ok"),
    ("B16", "verdict", r"\bverdict"),
    ("B17", "rb approve", r"rb approve"),
    ("B18", "Needs a human", r"needs a human"),
    ("B19", "a relative time", r"\bago\b"),
    ("W01", "agent", r"\bagents?\b"),
    ("W02", "human", r"\bhumans?\b"),
    ("W03", "session", r"\bsessions?\b"),
    ("W04", "via", r"\bvia\b"),
    ("W05", "detected:", r"detected:"),
    ("W06", "a you: line", r"\byou:"),
    ("W07", "by <name>", r"\brecorded by\b|\bby (?:human|agent|ana)\b"),
    ("W08", "a writer's id", r"\b(?:human|agent):[\w.-]+"),
    ("W09", "a product name", r"claude|codex|cursor(?!\s*:)|copilot|gemini|grok|chatgpt|openai|anthropic|windsurf"),
    ("W10", "the demo user's login", r"\bana\b"),
    ("W11", "a session id", r"\bsess-[\w-]+|\bthread-[\w-]+|golden-(?:session|wide|narrow)\b"),
    ("E01", "em dash", "\u2014"),
    ("P01", "adopt", r"\badopt(?:s|ed|ing|ion)?\b"),
    ("P02", "a label naming a source", r"agent-reported|session-derived|claude session|\bimported\b|\bunsupported\b"),
    ("P03", "verified", r"\bverif(?:y|ied|ies|ication)\b|evidence-backed|confidently reconstructed"),
    ("P04", "the exact code", r"\bthe exact code\b"),
    ("P05", "an absence not scoped to the record",
     r"\bno (?!recorded\b)(?:runs?|criteri(?:on|a)|experiments?|analys[ie]s|settings?|results?) (?:is read|reads?|cites?)\b"
     r"|\bnothing (?!in the record\b)(?:reads|cites)\b"
     r"|\b(?:nobody|no[ -]one)\b(?!(?: else)? recorded\b)"),
    ("P06", "files missing", r"\bfiles? missing\b|\bmissing files?\b"),
    ("P07", "runs recorded from the repository", r"runs recorded from the repository"),
    ("P08", "significance", r"significance unknown|nothing happened|\bsignifican"),
    ("S01", "frozen", r"\bfrozen\b|\bfreeze\b|\bunfrozen\b"),
    ("A01", "an agent command", r"rb run add|rb freeze|rb experiment add|rb spec set|--from results/"),
]
# rb's own words that a ban would catch, each kept by name or by rule, with its reason (LINT-P0.md, the owner's D5)
EXEMPT = [
    ("A5", r"a source contradicts a setting", "rb's computed phrase about a file and a setting, not about what someone says (surface.rs; the owner)"),
    ("A6", r"a priority call, not a refutation", "rb's words on a missed gate's call; they deny the word (ledger/views.rs; the owner)"),
    ("A4", r"a freeze locks them", "the \"not locked\" hint names the act, rb freeze, and says \"locks\" (page.rs)"),
    ("A7", r"\brestored or adopted\b|\brestore it or adopt the edit\b|rb doctor --adopt\b",
     "\"adopt\" where it names rb doctor --adopt, the command that owns the word (ledger/findings.rs)"),
    ("A8", r"\b\d+ verified\b|\bverified \d|\bverified:|\bverified(?= [\w./-]+\.(?:ya?ml|json|toml|txt|cfg|ini|py):\d)",
     "a setting's status, which rb computes by reading its source (AGENTS.md), here before that source's file and line"),
    ("A17", r"\bchanged (?:or missing )?since frozen\b|\bfrozen [^·]{1,40} · (?=\d+ of \d+ source)",
     "a repository manifest's own \"frozen\", the file's word, not rb's lock (repo.rs)"),
    ("SS12", r"metrics the runs report that no criterion reads", "an approved open-item head: absence over a record object (SHELL-SPEC 12)"),
    ("SS12b", r"\bthe same bytes are recorded by\b", "rb's same-bytes line names a run that recorded a file, not a writer (SHELL-SPEC 12)"),
    ("G1", r"\bverified(?= the named file, run or database row states)",
     "the glossary's term for a setting's status, before rb's line defining it (SHELL-SPEC 7.5)"),
]
# attributes that hold ids, not words
ID_ATTRS = {"class", "id", "href", "aria-labelledby", "aria-describedby", "aria-controls", "aria-owns", "aria-activedescendant", "aria-details",
            "aria-errormessage", "aria-flowto"}


class _Words(html.parser.HTMLParser):
    """rb's words on a page: its text and attribute values, without style, script and author text (class "au");
    the page's <title> apart, as an attribute is."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = []
        self.words, self.attrs, self.author, self.title = [], [], [], []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        voided = tag in Html.VOID
        if self.skip:
            if not voided:
                self.skip[-1][1] += 1
            return
        if tag in ("style", "script") or "au" in (a.get("class") or "").split():
            if not voided:
                self.skip.append([tag, 1, []])
            return
        self.in_title = tag == "title"
        self.attrs += [v for k, v in attrs if v and k not in ID_ATTRS]

    def handle_endtag(self, tag):
        self.in_title = False
        if self.skip:
            self.skip[-1][1] -= 1
            if self.skip[-1][1] == 0:
                kind, _, text = self.skip.pop()
                if kind not in ("style", "script"):
                    self.author.append(said("".join(text)))

    def handle_data(self, data):
        if self.skip:
            self.skip[-1][2].append(data)
        elif self.in_title:
            self.title.append(data)
        else:
            self.words.append(data)


def words_problems(page: str) -> list:
    """Each ban rb's words on `page` break: the text outside author text, and every attribute value but an id's, with
    the author text it holds left out (a crumb's title holds the project's title)."""
    w = _Words()
    w.feed(page)
    author = sorted({a for a in w.author if a}, key=len, reverse=True)

    def rbs(value: str) -> str:
        value = said(value)
        for a in author:
            value = value.replace(a, " ")
        return value

    out = []
    for where, s in [("text", said(" ".join(w.words)))] + [("attribute", rbs(v)) for v in w.attrs + ["".join(w.title)]]:
        for _, kept, _ in EXEMPT:
            s = re.sub(kept, " ", s, flags=re.I)
        for ident, name, rx in BANNED:
            for m in re.finditer(rx, s, flags=re.I):
                out.append(f"{where}: {ident} {name}: ...{s[max(0, m.start() - 40):m.end() + 40]}...")
    return out


# one phrase per ban, each a hit in rb's words
HITS = {
    "B01": "the result is supported", "B02": "a run contradicts it", "B03": "refuted by c2", "B04": "the evidence is established",
    "B05": "what the work claims", "B06": "the person decides", "B07": "a person decides", "B08": "Evidence clear", "B09": "Criterion missed",
    "B10": "Needs your call", "B11": "the command was executed", "B12": "spec frozen", "B13": "met on average", "B14": "Evidence inconclusive",
    "B15": "integrity ok", "B16": "the verdict", "B17": "rb approve c3", "B18": "Needs a human", "B19": "2 hours ago", "W01": "your agent",
    "W02": "a human made it", "W03": "this session", "W04": "via the terminal", "W05": "detected: git", "W06": "you: ana", "W07": "recorded by ana",
    "W08": "human:ana", "W09": "Gemini wrote it", "W10": "ana made it", "W11": "from sess-1a2b", "E01": "open \u2014 3",
    "P01": "the record's adoption", "P02": "agent-reported runs", "P03": "the reading was verified", "P04": "the exact code is known",
    "P05": ("nothing reads this resource", "c4 misses its rule and nobody has decided on it", "no one checked it"),
    "P06": "files missing: 2", "P07": "Runs recorded from the repository (2)",
    "P08": "significance is low", "S01": "the frozen spec changed", "A01": "rb run add e1 --from results/x",
}
# rb's own words that stand, each with the ban it would otherwise break
KEPT = {
    "A5": "integrity: a source contradicts a setting and 1 more", "A6": "e2 gate missed: a priority call, not a refutation",
    "A4": "criteria and settings can still change; a freeze locks them", "A7": "run runs2 was edited outside rb; it is not read until restored or adopted",
    "A8": "settings: 2 per-run · 1 verified · quant int8 verified configs/int8.yaml:1",
    "A17": "manifest.json: frozen 2026-10-01 · 3 of 4 source hashes match; changed or missing since frozen: scripts/train.py",
    "SS12": "Metrics the runs report that no criterion reads · 1",
    "SS12b": "int8 · calib images 512: the same bytes are recorded by fp32 and int8 · seed 1 (e1), which made ckpt-int8-s1.",
    "G1": "verified the named file, run or database row states this setting's value.",
}


def test_rbs_words_only(record):
    """The page holds none of the banned words in rb's own text or attributes (an aria label included); author text, in
    its own element, is the record's and is not linted, in an attribute too; each named exemption stands, and nothing
    beside it."""
    page = page_of(record)
    assert not words_problems(page), words_problems(page)[:10]
    assert {i for i, _, _ in BANNED} == set(HITS) and {i for i, _, _ in EXEMPT} == set(KEPT)
    for ident, phrases in HITS.items():
        for phrase in (phrases,) if isinstance(phrases, str) else phrases:
            for html_ in (f"<p>{phrase}</p>", f'<nav aria-label="{phrase}"></nav>', f'<p data-hint="{phrase}"></p>'):
                hits = words_problems(html_)
                assert any(h.split(":")[1].split()[0] == ident for h in hits), (ident, html_, hits)
            assert not words_problems(f'<p class="au">{phrase}</p>'), ident
    # an absence scoped to the record stands
    for scoped in ("When rb finds a run nobody recorded, it appears here.", "at a time nobody recorded", "1 changed file nothing in the record cites",
                   "no recorded run reads this comparison"):
        assert not words_problems(f"<p>{scoped}</p>"), scoped
    for ident, phrase in KEPT.items():
        assert not words_problems(f"<p>{phrase}</p>"), (ident, words_problems(f"<p>{phrase}</p>"))
        assert words_problems(f"<p>{phrase}; the run was frozen and verified</p>"), ident
    assert not words_problems('<section aria-labelledby="h-session"><h1 id="h-session">Runs</h1></section>')
    # a setting's status before its source's file and line stands; "verified" before any other file:line is a claim
    assert not words_problems("<p>quant int8 verified configs/int8.yaml:1</p>")
    assert words_problems("<p>the result was verified README.md:3</p>")
    doc = example()
    doc["home"]["began"] = "4 calls wait on you; the evidence is established."
    doc["home"]["open_line"] = "open in the record \u2014 3"
    doc["questions"][0]["text"] = "Is it established that INT8 holds, as an agent claims?"
    doc["project"]["title"] = "INT8 ResNet-50: an agent's frozen claims"
    doc["home"]["scope"] += " a source contradicts a setting; 20 verified"
    doc["home"]["uncited_line"] = "No run reads it."
    doc["calls"][0]["reason"]["detail"] = "the exact code is known"
    hits = words_problems(page_of(record, doc))
    assert any("established" in h and "the evidence is" in h for h in hits), hits
    assert any("em dash" in h for h in hits), hits
    assert any("absence" in h for h in hits), hits
    assert any("the exact code" in h and h.startswith("text") for h in hits), hits
    assert not any("INT8 holds" in h or "frozen claims" in h for h in hits), hits
    assert not any("contradicted" in h or "verified" in h for h in hits), hits


def test_rbs_words_only_on_a_record_with_a_lock_a_conflict_and_a_hand_edit(record):
    """LINT-P0 part B: phase 0's demo-plus (a locked spec, a source that conflicts with a setting, a run file edited by
    hand) puts rb's lock, conflict and adoption words where the Overview and Your call draw them. The lint passes what
    stands (each exemption) and catches rb's old freeze words wherever they are drawn."""
    doc = example()
    doc["calls"][1]["reason"]["also"] = "integrity: a source contradicts a setting and 1 more"
    doc["open_items"][1]["items"].append(dict(doc["open_items"][1]["items"][0], what="run runs2b was edited outside rb: int8.top1 changed; it is not read until restored or adopted"))
    doc["open_items"][1]["items"].append(dict(doc["open_items"][1]["items"][0], what="manifest.json: frozen 2026-10-01 · 3 of 4 source hashes match; changed or missing since frozen: scripts/train.py"))
    design = next(r for r in doc["pending"]["rows"] if r["id"] == "e1")
    # a design's change lines are drawn on its experiment's view (a page says the waiting design by its one line)
    mine = doc["experiments"][0]["design"]
    design["lines"] = mine["lines"] = ["changed since you confirmed it: locked 2026-10-19 · note: locked before the seed sweep"]
    page = page_of(record, doc)
    assert "restored or adopted" in page and "a source contradicts a setting" in page and "locked 2026-10-19" in page
    assert not words_problems(page), words_problems(page)
    design["lines"] = mine["lines"] = ["changed since you confirmed it: frozen 2026-10-19 · note: locked before the seed sweep"]
    doc["calls"][1]["reason"]["also"] = "basis: typed · spec frozen"
    hits = words_problems(page_of(record, doc))
    assert any("S01" in h and "frozen 2026-10-19" in h for h in hits) and any("B12" in h for h in hits), hits


# ---------------------------------------------------------------- the test hook

def test_the_shell_draws_rb_page_and_rb_export_whatever_the_hook_says(record):
    """Since the switch (BP 2c) rb page and rb export draw the shell's frame whatever RB_TEST_SHELL says, and never the
    page they drew before (page.rs's tabs)."""
    model = record / "hook.json"
    model.write_text(json.dumps(example()), encoding="utf-8")
    for value in (None, "0", "1", "yes"):
        env = {"RB_TEST_SHELL_MODEL": str(model)} | ({"RB_TEST_SHELL": value} if value else {})
        out = record / f"hook-{value}.html"
        r = rb(record, "page", "--out", str(out), **env)
        assert r.returncode == 0, r.stderr
        page = out.read_text(encoding="utf-8")
        assert 'id="nav"' in page and 'role="tablist"' not in page, value
        bundle = record / f"hook-{value}-export"
        r = rb(record, "export", "--out", str(bundle), **env)
        assert r.returncode == 0, r.stderr
        page = (bundle / "page.html").read_text(encoding="utf-8")
        assert 'id="nav"' in page and 'role="tablist"' not in page, value


def test_a_shell_page_is_rbs_own(record):
    """The shell's page carries rb's page mark, so rb page writes over a page it made and rb export recognises it."""
    model = record / "own.json"
    model.write_text(json.dumps(example()), encoding="utf-8")
    out = record / "own.html"
    for _ in range(2):
        r = rb(record, "page", "--out", str(out), RB_TEST_SHELL="1", RB_TEST_SHELL_MODEL=str(model))
        assert r.returncode == 0, r.stderr
    page = out.read_text(encoding="utf-8")
    assert page.startswith("<!doctype html>") and PAGE_MARK + "2026-10-19 09:41 UTC" in said(page)


@pytest.mark.parametrize("raw, said_", [
    ('{"contract": "rb-shell-view/2.0", "tier": "owner"}', "rb-shell-view/2.0"),
    ('{"tier": "owner"}', "names no contract"),
    ('[1, 2]', "JSON object"),
    (json.dumps(example("live")), "this one is tier 'live'"),
], ids=["another-major", "no-contract", "not-an-object", "live"])
def test_a_document_rb_cannot_draw_is_refused(record, raw, said_):
    """A document of another major version, one naming no contract, one that is not an object, or rb open's own
    document given to a page is an error in rb's words, never a page; what to do fits it (a test's document, since
    only a test hands the shell one), never the hint for a record's values."""
    for surface in ("page", "export"):
        page, error = drawn(record, raw=raw, surface=surface)
        assert not page and said_ in error, error
        assert "RB_TEST_SHELL_DUMP=<file> writes it" in error and "rb schema <kind>" not in error, error


# ---------------------------------------------------------------- the frame

def test_one_honesty_line(record):
    """The footer carries the honesty line, once on the page, outside any fold."""
    for surface in ("page", "export"):
        page = page_of(record, surface=surface)
        assert page.count(HONEST) == 1, surface
        t = Html(page)
        foot = next(e for e in t.all() if e["tag"] == "footer")
        holder = next(e for e in t.all(foot) if HONEST in t.text(e) and e["tag"] == "p")
        up, folded = holder, False
        while up is not None:
            folded |= up.get("tag") == "details"
            up = up.get("up")
        assert not folded


def test_the_nav_and_its_counts(record):
    """The nav: Overview, Research with each question whole, All criteria, Experiments by id and short title, Record
    (Runs, Resources, History), and Your call (N) at the foot, each count rb's."""
    t = Html(page_of(record))
    nav = t.by_id("nav")
    links = [(e["attrs"]["href"], said(t.text(e))) for e in t.all(nav) if e["tag"] == "a"]
    hrefs = [h for h, _ in links]
    assert hrefs == ["#overview", "#research", "#q-q1", "#q-q1a", "#criteria", "#experiments", "#x-e1", "#x-e2", "#runs", "#resources",
                     "#history", "#your-call"]
    shown = dict(links)
    assert shown["#research"] == "Research 2" and shown["#criteria"] == "Rules 6" and shown["#runs"] == "Runs 9"
    # an experiment by its short title, its id trailing (design pass 1, P3)
    assert shown["#x-e2"] == "calibration set size e2" and shown["#q-q1a"].startswith("↳ How many")
    # the user's calls alone: what waits for a confirmation is never under Your call (AGENTS.md)
    assert shown["#your-call"] == "Your call 4"


def _queue_as_rb_gives_it(doc: dict) -> dict:
    """`doc` with its home.queue (contract 1.6) kept to what the document still holds, as M's queue_of words it: the rows
    of the calls left, the designs and other confirmations left, rb's found count and the uncited count."""
    home, calls = doc["home"], {c["subject"] for c in doc["calls"]}
    pending = {r["id"].split("@")[0] if r["kind"] == "design" else r["id"] for r in doc["pending"]["rows"]}
    found = max(home.get("found_total") or 0, len(home.get("found") or []))
    queue = []
    for row in home["queue"]:
        kind = row["kind"]
        if kind in ("baseline", "call") and row["target"] not in calls:
            continue
        if kind in ("design", "pending_call") and not set(row["targets"]) & pending:
            continue
        if kind == "found":
            if not found:
                continue
            row = dict(row, title="Record a result found in the repository" if found == 1 else f"Record {found} results found in the repository")
        if kind == "uncited" and not home.get("uncited"):
            continue
        queue.append(row)
    home["queue"] = queue
    return doc


def test_your_calls_count_is_the_users_calls_and_the_queue_heading_counts_nothing(record):
    """OVERVIEW-V2's queue holds everything rb cannot settle and the user can; its heading counts nothing (FIX4); Your call's badge and
    heading count the user's calls alone (your_call.n): a proposed or past call and a design waiting for a confirmation
    are listed apart, never under Your call (AGENTS.md, "Whose call it is"; test_call_authority's pins). Five found
    items are one row."""
    doc = example()
    doc["home"]["found"] = [dict(doc["home"]["found"][0], request_id=f"f{i}", title=f"Record results/seed{i}/metrics.json on e1") for i in range(5)]
    doc["home"]["found_total"] = 5
    doc["your_call"]["n"] = 3
    _queue_as_rb_gives_it(doc)
    t = Html(page_of(record, doc))
    rows = t.of_class("qrow", t.by_id("queue"))
    # rb's queue in its rank order, a row each as rb gives it (contract 1.6)
    assert [r["attrs"]["data-kind"] for r in rows] == [r["kind"] for r in doc["home"]["queue"]] == [
        "baseline", "call", "call", "call", "design", "pending_call", "pending_call", "found", "uncited"]
    assert said(t.text(t.by_id("h-queue"))) == "Needs your judgment"  # FIX4: the line under it counts
    nav_yc = next(e for e in t.all(t.by_id("nav")) if e["tag"] == "a" and e["attrs"]["href"] == "#your-call")
    assert said(t.text(nav_yc)) == "Your call 3" and said(t.text(t.by_id("h-your-call"))) == "Your call (3)"
    assert said(t.text(rows[7])).startswith("Record 5 results found in the repository")


def test_headings_never_skip(record):
    """Down the page, each heading is at most one level below the one before it, and each view starts at h1."""
    t = Html(page_of(record))
    levels = [int(e["tag"][1]) for e in t.all() if re.fullmatch(r"h[1-6]", e["tag"])]
    assert levels[0] == 1
    assert all(b <= a + 1 for a, b in zip(levels, levels[1:])), levels
    for view in (e for e in t.all() if t.has(e, "view")):
        first = next(e for e in t.all(view) if re.fullmatch(r"h[1-6]", e["tag"]))
        assert first["tag"] == "h1", view["attrs"]["id"]


def test_every_link_lands_and_no_id_on_a_fold(record):
    """Every link inside the page lands on an element of it, ids are unique, and no id sits on a <details>."""
    for doc in (example(), _calls_everywhere()):
        t = Html(page_of(record, doc))
        ids = [e["attrs"]["id"] for e in t.all() if "id" in e["attrs"]]
        assert len(ids) == len(set(ids)), [i for i in ids if ids.count(i) > 1]
        dead = sorted({e["attrs"]["href"] for e in t.all() if e["tag"] == "a" and e["attrs"].get("href", "").startswith("#")
                       and e["attrs"]["href"][1:] not in ids})
        assert not dead, dead
        assert not [e for e in t.all() if e["tag"] == "details" and "id" in e["attrs"]]


def _calls_everywhere() -> dict:
    """The example with a call on a question rb's questions do not list, and one filed under nothing."""
    doc = example()
    extra = copy.deepcopy(doc["calls"][0])
    extra.update(subject="c9", question="q7", title="a criterion on a question not listed")
    nowhere = copy.deepcopy(doc["calls"][1])
    nowhere.update(subject="c8", question="", title="a criterion filed nowhere")
    doc["calls"] += [extra, nowhere]
    return doc


# ---------------------------------------------------------------- the Overview

def test_the_overview_top_to_bottom(record):
    """OVERVIEW-V2: the title and its one line; a standing card per top-level question; Evidence integrity; Needs your
    judgment, with what waits for a confirmation folded under it; the research questions; then, folded, how rb reads
    this repository, the recent changes and the notes. No second copy of the calls (no rail) and no command in view."""
    page = page_of(record)
    t = Html(page)
    ov = t.by_id("overview")
    marks = ("st-q1", "integrity", "queue", "ov-questions", "how-made")
    order = [e["attrs"]["id"] for e in t.all(ov) if e["attrs"].get("id") in marks]
    assert order == list(marks)
    folds = [e["attrs"].get("data-fold") for e in t.all(ov) if e["tag"] == "details" and (e["up"] is ov or e["up"]["up"] is ov)]
    assert folds == ["to-confirm", "how-made", "recent", "notes"], folds
    assert t.by_id("ov-calls") is None and t.inside(t.by_id("found"), t.by_id("how-made"))
    assert not [e for e in t.all(ov) if "data-rail" in e["attrs"]]
    # no command in view: the confirmations' commands are one click down, in their fold
    assert all(t.inside(e, t.by_id("to-confirm")) for e in t.all(ov) if e["tag"] == "pre"), "no command on the Overview"
    line = said(t.text(t.of_class("ovh", ov)[0]))
    assert line == f"{example()['home']['counts']['runs']} recorded runs · changed {example()['home']['last_write']['at']}"
    # the absolute time, which rb open's live view says relative in the browser from its datetime
    when = next(e for e in t.all(t.of_class("ovh", ov)[0]) if e["tag"] == "time")
    assert when["attrs"]["datetime"] == example()["home"]["last_write"]["at"].replace(" UTC", "Z").replace(" ", "T") and "data-changed" in when["attrs"]


def test_a_standing_card_per_top_level_question(record):
    """A standing card for each top-level question: rb's state word with its hint, the question in its words (author
    text) linked to its view, rb's line on why, and the non-zero tallies, short, each in rb's colour and linked to its
    reading. No sub-question, hypothesis or setting in it; its sub-questions are rows below, each once."""
    doc = example()
    t = Html(page_of(record, doc))
    ov = t.by_id("overview")
    cards = t.of_class("stand", ov)
    assert [c["attrs"]["id"] for c in cards] == ["st-q1"]
    card = cards[0]
    standing = doc["home"]["standing"][0]
    # its state names its subject (plain words pass 3)
    assert said(t.text(t.of_class("sth", card)[0])) == "Open question" and standing["state_word"] == "Open" and t.of_class("term", t.of_class("sth", card)[0])
    stq = t.of_class("stq", card)[0]
    assert said(t.text(stq)) == doc["questions"][0]["text"] and t.of_class("au", stq)
    assert next(e for e in t.all(stq) if e["tag"] == "a")["attrs"]["href"] == "#q-q1"
    assert said(t.text(t.of_class("str", card)[0])) == said(standing["line"])
    assert said(t.text(t.of_class("stt", card)[0])) == "Rules: 3 met · 1 not reproduced"
    tallies = [(e["attrs"].get("class"), said(t.text(e)), e["attrs"].get("href")) for e in t.all(t.of_class("stt", card)[0]) if t.has(e, "sw")]
    assert tallies == [("sw good", "3 met", "#q-q1:rd:criteria-met"), ("sw bad", "1 not reproduced", "#q-q1:rd:not-reproduced")]
    # rb's tallies are the card's: where rb gives none, none is drawn, never the question's older counts
    bare = copy.deepcopy(doc)
    bare["home"]["standing"][0]["tallies"] = []
    assert not Html(page_of(record, bare)).of_class("stt", Html(page_of(record, bare)).by_id("st-q1"))
    for other in (doc["questions"][1]["text"], doc["experiments"][0].get("hypothesis") or "no hypothesis"):
        assert said(other) not in said(t.text(card))
    rows = t.of_class("qr", t.by_id("ov-questions"))
    assert [r["attrs"]["data-go"] for r in rows] == ["q-q1a"]
    assert doc["questions"][1]["status"] == "OPEN · no call yet"
    assert said(t.text(rows[0])) == f"{doc['questions'][1]['text']} Open question · Rules: 1 not met"
    assert said(t.raw(ov)).count(said(doc["questions"][0]["text"])) == 1


def test_a_standing_cards_interpretation_is_one_line_with_its_tag(record):
    """PLAIN-WORDS-PASS-3: a recorded interpretation shows on its card as a note in one line, its state in the line and
    never a free-floating tag: "Note: <its words>." once confirmed, "A note, not confirmed: <its words>." until then,
    the label with the glossary's line for a note; then whether its rules hold, as a sentence of its own ("Its 2 rules
    are met."). An inactive one is not drawn."""
    doc = example()
    t = Html(page_of(record, doc))
    card = t.by_id("st-q1")
    line = t.of_class("sti", card)[0]
    given = doc["home"]["standing"][0]["interpretation"]
    assert given["holds"] == "its 2 rules met"
    assert said(t.text(line)) == f"Note: {given['text']} Its 2 rules are met."
    assert [said(t.text(e)) for e in t.of_class("au", line)] == [given["text"]]
    label = t.of_class("k", line)[0]
    assert said(t.text(label)) == "Note:" and label["up"]["attrs"]["aria-describedby"] == "g-note-on-a-question"
    assert not t.of_class("tag", card)
    given["confirmed"] = False
    t = Html(page_of(record, doc))
    line = t.of_class("sti", t.by_id("st-q1"))[0]
    assert said(t.text(line)) == f"A note, not confirmed: {given['text']} Its 2 rules are met."
    assert not t.of_class("tag", t.by_id("st-q1"))
    # an inactive one rb gives as empty: none is drawn, and nothing from the question's lead in its place
    doc["home"]["standing"][0]["interpretation"] = {"line": "", "text": "", "holds": "", "confirmed": False}
    t = Html(page_of(record, doc))
    assert not t.of_class("sti", t.by_id("st-q1"))


def _led_by_a_comparison() -> dict:
    """The example with q1 led by rb's computed comparison, which raises its own qualification."""
    doc = example()
    ov = doc["questions"][0]["overview"]
    ov["relations"] = [{"summary": "The measured direction reverses; repeatability remains uncertain.", "outcome": "observed_reversal",
                        "comparisons": [{"source": "c1", "target": "c3"}]}]
    ov["limits"].append({"label": "Reversal observed; repeatability uncertain", "criteria": ["c1"], "relations": [0]})
    doc["questions"][0]["lead"] = {"kind": "relation", "index": 0, "text": ov["relations"][0]["summary"], "active": True, "confirmed": True}
    # M's standing_of gives an interpretation only where one leads
    doc["home"]["standing"][0]["interpretation"] = {"line": "", "text": "", "holds": "", "confirmed": False}
    return doc


def test_a_card_led_by_a_computed_comparison(record):
    """SHELL-SPEC 4.1.1 on the standing card: a question led by rb's computed comparison draws its sentence once, in
    rb's voice, outside author text, so the word lint reads it."""
    doc = _led_by_a_comparison()
    t = Html(page_of(record, doc))
    card = t.by_id("st-q1")
    line = said(t.raw(t.of_class("sti", card)[0]))
    assert line == "Computed comparison: The measured direction reverses; repeatability remains uncertain. · 1 declared comparison"
    assert not [e for e in t.of_class("au", card) if "direction reverses" in t.text(e)]
    assert said(t.raw(t.by_id("overview"))).count("direction reverses") == 1
    doc["questions"][0]["lead"]["text"] = "The measured direction reverses; repeatability is established."
    assert any(" B04 " in h and "repeatability is established" in h for h in words_problems(page_of(record, doc)))


def test_where_rb_looked_is_rbs_line(record):
    """The made block says where the record started, rb's scope line and the uncited count, each verbatim from rb."""
    home = example()["home"]
    block = view_text(page_of(record), "how-made")
    for line in (home["began"], home["scope"], home["uncited_line"]):
        assert said(line) in block
    assert block.index(said(home["began"])) < block.index(said(home["scope"])) < block.index(said(home["uncited_line"]))


def test_how_rb_reads_this_repository_is_folded(record):
    """OVERVIEW-V2: "How rb reads this repository" is one fold: where the record started, rb's scope line (which rb
    open's live module says again in place), the changed files nothing cites, and what rb found, each verbatim from rb;
    with no scope line, rb's short one, said again in place too."""
    doc = example()
    home = doc["home"]
    t = Html(page_of(record, doc))
    fold = next(e for e in t.by_id("how-made")["kids"] if isinstance(e, dict))
    assert fold["tag"] == "details" and fold["attrs"]["data-fold"] == "how-made"
    summary = next(e for e in fold["kids"] if isinstance(e, dict))
    assert summary["tag"] == "summary" and said(t.text(summary)) == "How rb reads this repository"
    inside = [said(t.raw(e)) for e in fold["kids"] if isinstance(e, dict) and e["tag"] == "p"]
    assert inside == [said(home["began"]), said(home["scope"]), said(home["uncited_line"])]
    assert [e["attrs"].get("data-live") for e in fold["kids"] if isinstance(e, dict) and e["tag"] == "p"] == [None, "scope", None]
    assert t.inside(t.by_id("found"), fold)
    home["scope"] = ""
    t = Html(page_of(record, doc))
    short = next(e for e in t.all(t.by_id("how-made")) if e["attrs"].get("data-live") == "scope_short")
    assert said(t.text(short)) == said(home["scope_short"])
    # its time is the clock's, in the span rb sync --watch reads as the clock's (MP-1022-03, L's clock_free)
    stamp = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC", home["scope_short"]).group(0)
    assert [said(t.text(e)) for e in t.of_class("nw", short)] == [stamp]


def test_evidence_integrity_says_each_part_once(record):
    """OVERVIEW-V2: Evidence integrity, shown only when something weakens the whole body of evidence: each part of rb's
    line on how the record was made one plain line, the criteria linked to All criteria, "command (as given)" with its
    hint, the retracted runs linked to Runs; the document's own parts and its line on what they affect where it gives
    them; none when nothing weakens it. The record-start line's parts link inside the fold that holds it."""
    doc = example()
    t = Html(page_of(record, doc))
    box = t.by_id("integrity")
    given = doc["home"]["integrity"]
    lines = [said(t.text(e)) for e in box["kids"] if isinstance(e, dict) and e["tag"] == "p"]
    assert lines == [said(p["line"]) for p in given["parts"]] + [said(given["affects"])]
    # each part links to the list it counts: the criteria to All criteria, the runs to Runs
    to = {"after_evidence": "#criteria", "without_command": "#runs", "retracted": "#runs"}
    assert [(e["attrs"]["href"], said(t.text(e))) for e in t.all(box) if e["tag"] == "a"] == [(to[p["kind"]], said(p["line"])) for p in given["parts"]]
    # with no part of rb's own, rb's line on how the record was made, a part a line, "command (as given)" with its hint
    del doc["home"]["integrity"]
    t = Html(page_of(record, doc))
    box = t.by_id("integrity")
    lines = [said(t.text(e)) for e in box["kids"] if isinstance(e, dict) and e["tag"] == "p"]
    assert lines == ["2 of 6 rules were written after their results came in", "1 of 8 runs records its command (as given)", "1 run retracted"]
    assert [(e["attrs"]["href"], said(t.text(e))) for e in t.all(box) if e["tag"] == "a"] == [
        ("#criteria", "2 of 6 rules were written after their results came in"), ("#runs", "1 run retracted")]
    hinted = t.of_class("term", box)
    assert [said(t.text(e)) for e in hinted] == ["command (as given)"] and "rb did not run it" in hinted[0]["attrs"]["data-hint"]
    doc["home"]["integrity"] = {"parts": [{"code": "late", "line": "9 of 13 criteria were declared after their evidence existed.", "n": 9, "target": "criteria"}],
                                "affects": "Read them as a description of what happened, not as a test."}
    t = Html(page_of(record, doc))
    box = t.by_id("integrity")
    assert [said(t.text(e)) for e in box["kids"] if isinstance(e, dict) and e["tag"] == "p"] == [
        "9 of 13 criteria were declared after their evidence existed.", "Read them as a description of what happened, not as a test."]
    del doc["home"]["integrity"]
    doc["home"]["made"].update(after_evidence=0, with_command=8, line="0 of 6 criteria were declared after their evidence existed")
    doc["home"]["counts"]["retracted"] = 0
    assert Html(page_of(record, doc)).by_id("integrity") is None
    doc = example()
    began = doc["home"]["began"]
    day, runs = "2026-10-06", "after 1 of its 8 runs was made"
    i, j = began.index(day), began.index(runs)
    doc["home"]["began_parts"] = [[began[:i], ""], [day, "history"], [began[i + len(day):j], ""], [runs, "runs"], [began[j + len(runs):], ""]]
    t = Html(page_of(record, doc))
    fold = next(e for e in t.by_id("how-made")["kids"] if isinstance(e, dict))
    first = next(e for e in fold["kids"] if isinstance(e, dict) and e["tag"] == "p")
    assert said(t.raw(first)) == said(began)
    assert [(e["attrs"]["href"], said(t.text(e))) for e in t.all(first) if e["tag"] == "a"] == [("#history", day), ("#runs", runs)]


def test_found_apart_and_capped(record):
    """Found in the repository: one queue row that counts it, its list in the Overview's fold How rb reads this
    repository (rb's count, ten items with rb's hint, then "and N more"), never a row each and never in Your call; while
    any is found, nothing on the page says nothing waits on you."""
    doc = example()
    doc["home"]["found"] = [dict(doc["home"]["found"][0], request_id=f"f{i}", title=f"Record results/seed{i}/metrics.json on e1") for i in range(23)]
    doc["home"]["found_total"] = 23
    _queue_as_rb_gives_it(doc)
    page = page_of(record, doc)
    t = Html(page)
    for at in ("found",):
        found = t.by_id(at)
        assert said(t.text(t.by_id(f"h-{at}"))) == "Found in the repository (23)"
        items = [e for e in t.all(found) if e["tag"] == "li"]
        assert len(items) == 10 and all("found by rb watching the repository" in t.text(i) for i in items)
        assert "and 13 more" in said(t.text(found))
    assert t.inside(t.by_id("found"), t.by_id("how-made")) and t.by_id("yc-found") is None and "seed0" not in view_text(page, "your-call")
    row = next(r for r in t.of_class("qrow", t.by_id("queue")) if r["attrs"]["data-kind"] == "found")
    assert said(t.text(row)) == "Record 23 results found in the repository for int8 against fp32 Inspect" and "seed0" not in t.raw(t.by_id("queue"))
    assert "nothing waits" not in said(page).lower()
    doc["home"]["found_total"] = 10
    doc["home"]["found"] = doc["home"]["found"][:10]
    assert "more" not in view_text(page_of(record, doc), "found")


@pytest.mark.parametrize("waits", ["found", "pending"])
def test_nothing_says_nothing_waits_while_something_does(record, waits):
    """Guarantee 17 on the shell: with no call waiting on you, Your call reads (0), and while a found item or a
    confirmation waits, nothing on the page says nothing waits; what waits is the queue's row and its own fold."""
    doc = example()
    doc["calls"], doc["your_call"]["n"] = [], 0
    doc["home"].pop("quiet", None)
    doc["home"]["uncited"] = 0
    for q in doc["questions"]:
        q["calls"] = []
    doc["unfiled"]["calls"] = []
    if waits == "found":
        doc["home"]["found"], doc["home"]["found_total"] = doc["home"]["found"][:1], 1
        doc["pending"]["rows"] = []
    else:
        doc["home"]["found"], doc["home"]["found_total"] = [], 0
    _queue_as_rb_gives_it(doc)
    page = page_of(record, doc)
    t = Html(page)
    assert "nothing waits" not in said(page).lower() and "nothing waits" not in said(t.raw(t.root)).lower()
    assert said(t.text(t.by_id("h-your-call"))) == "Your call (0)"
    # one row for what rb found; one for the designs and one for each other confirmation
    n = 1 if waits == "found" else 1 + len([r for r in doc["pending"]["rows"] if r["kind"] != "design"])
    assert said(t.text(t.by_id("h-queue"))) == "Needs your judgment" and len(doc["home"]["queue"]) == n
    if waits == "found":
        assert said(t.text(t.by_id("h-found"))) == "Found in the repository (1)" and t.by_id("to-confirm") is None
    else:
        assert t.by_id("found") is None and len([e for e in t.all(t.by_id("to-confirm")) if e["tag"] == "li"]) == len(doc["pending"]["rows"])
    doc["pending"]["rows"], doc["home"]["found"], doc["home"]["found_total"] = [], [], 0
    t = Html(page_of(record, _queue_as_rb_gives_it(doc)))
    assert "Nothing waits on your judgment." in said(t.text(t.by_id("queue")))


def test_waiting_for_confirmation_apart(record):
    """What waits for a confirmation is folded under the Overview's queue, in its four groups, each line ending in its
    one rb confirm in the user's own terminal; the queue's row says it with no command, hash or path; never in Your call
    or a call's row (AGENTS.md: listed apart, never under Your call); rb's confirm-all line only while no design waits."""
    doc = example()
    page = page_of(record, doc)
    t = Html(page)
    wait = t.by_id("to-confirm")
    # inside its fold, so a link to it opens the fold around it
    fold = wait["up"]
    assert fold["tag"] == "details" and "open" not in fold["attrs"] and t.inside(wait, t.by_id("overview"))
    assert said(t.text(next(k for k in fold["kids"] if isinstance(k, dict)))) == "Waiting for your confirmation"
    for r in doc["pending"]["rows"]:
        assert said(r["text"]) not in view_text(page, "your-call") and r["confirm"] not in view_text(page, "your-call")
    assert [said(t.text(e)) for e in t.all(wait) if e["tag"] == "h3"] == [
        "Proposed, not made (1)", "Past calls mentioned, not confirmed (1)", "Experiment designs not confirmed (2)"]
    lis = [e for e in t.all(wait) if e["tag"] == "li"]
    assert len([r for r in lis if t.has(r, "wd")]) == len([r for r in doc["pending"]["rows"] if r["kind"] == "design"]) > 0
    for row in lis:
        kids = [k for k in row["kids"] if isinstance(k, dict)]
        assert t.has(kids[-1], "cmdbox") and said(t.text(kids[-1])).startswith("rb confirm ")
        if t.has(row, "wd"):
            assert len(kids) == 2 and next(e for e in t.all(kids[0]) if e["tag"] == "a")["attrs"]["href"].startswith("#x-")
            assert re.fullmatch(r"rb confirm [\w.-]+", said(t.text(kids[-1]))), t.text(kids[-1])
        else:
            assert t.has(kids[-2], "cf") and said(t.text(kids[-2])) == "confirm in your own terminal:"
    queue = t.by_id("queue")
    # each confirmation's row as rb gives it (contract 1.6): the designs one row by their titles, each other call a row
    # by its action and subject, with no command, hash or path
    drawn = [said(t.text(r)) for r in t.of_class("qrow", queue) if r["attrs"]["data-kind"] in ("design", "pending_call")]
    given = [r for r in doc["home"]["queue"] if r["kind"] in ("design", "pending_call")]
    assert drawn == [said(f"{r['title']} {r['detail']} Review") for r in given]
    assert drawn[0] == "Confirm 2 experiment designs int8 against fp32 · calibration set size Review"
    past = next(r["text"] for r in doc["pending"]["rows"] if r["kind"] == "past")
    assert ".md" in past and not [d for d in drawn if ".md" in d or "@" in d]
    assert "rb confirm" not in t.raw(t.by_id("queue"))
    rows = doc["pending"]["rows"]
    d1, d2 = (r for r in rows if r["kind"] == "design")
    d1["confirm"], d2["confirm"] = "cd /p/wt-b && " + d1["confirm"], ""
    t = Html(page_of(record, doc))
    wds = t.of_class("wd", t.by_id("to-confirm"))
    assert said(t.text(t.of_class("cmdbox", wds[0])[0])) == f"cd /p/wt-b && rb confirm {d1['id'].split('@')[0]}"
    assert not t.of_class("cmdbox", wds[1])
    for row in t.of_class("crow") + t.of_class("callrow"):
        assert "rb confirm" not in t.text(row)
    doc = example()
    doc["pending"]["confirm_all"] = "Confirm all 2 waiting at once: rb confirm --all"
    assert "rb confirm --all" not in said(page_of(record, doc))
    doc["pending"]["rows"] = [r for r in doc["pending"]["rows"] if r["kind"] != "design"]
    assert "rb confirm --all" in view_text(page_of(record, doc), "to-confirm")
    gate = dict(doc["pending"]["rows"][0], id="e2@1a2b", kind="gate", text="e2's gate: if it holds, ship int8", confirm="rb confirm e2@1a2b")
    odd = dict(doc["pending"]["rows"][0], id="dec8", kind="relation", text="a relation, proposed", confirm="rb confirm dec8")
    doc["pending"]["rows"] += [gate, odd]
    t = Html(page_of(record, doc))
    heads = [said(t.text(e)) for e in t.all(t.by_id("to-confirm")) if e["tag"] == "h3"]
    assert heads[-2:] == ["Gates set, not confirmed (1)", "Also waiting for your confirmation (1)"], heads


def test_no_agent_record_commands(record):
    """The page never prints a command that records work (rb run add, rb experiment add, rb freeze, rb spec set): a
    found item's command stays rb ui's. The word lint holds the same commands, so the real model's page is checked for
    them at each merge point."""
    page = page_of(record)
    for cmd in ("rb run add", "rb experiment add", "rb freeze", "rb spec set", "--from results/"):
        assert cmd not in page, cmd
        assert words_problems(f"<pre class=\"cmd\">{cmd} e1</pre>"), cmd


# ---------------------------------------------------------------- Your call

def test_calls_lead_with_a_reason(record):
    """Every call, in a question's rail, Research's work filed under no question and Your call, leads with why rb asks:
    its reason's label, before what it is about. The Overview's queue says each as rb's verb phrase (contract 1.6's
    home.queue, OVERVIEW-V2: a row is a verb phrase, a line of numbers or names, and one action)."""
    doc = example()
    t = Html(page_of(record, doc))
    rows = t.of_class("callrow") + [s for s in (next(k for k in r["kids"] if isinstance(k, dict)) for r in t.of_class("crow"))]
    # the question rails (3) and Your call (4); Research's unfiled work lists no call its rows lead to
    assert len(rows) == 3 + 4
    labels = {c["reason"]["label"] for c in doc["calls"]}
    for row in rows:
        first = next(e for e in t.all(row))
        if t.has(row, "cr"):
            first = next(e for e in t.all(first))
        assert t.has(first, "rsn"), row
        assert said(t.text(t.of_class("rl", first)[0])) in labels
    calls = [r for r in t.of_class("qrow", t.by_id("queue")) if r["attrs"]["data-kind"] in ("baseline", "call")]
    given = [r for r in doc["home"]["queue"] if r["kind"] in ("baseline", "call")]
    assert [said(t.text(t.of_class("qv", r)[0])) for r in calls] == [said(r["title"]) for r in given] and given


def _calls_in_queue_order(doc: dict) -> list:
    """The document's calls in rb's queue order (contract 1.6's home.queue), then any the queue does not hold."""
    order = [r["target"] for r in doc["home"]["queue"] if r["kind"] in ("baseline", "call")]
    return sorted(doc["calls"], key=lambda c: order.index(c["subject"]) if c["subject"] in order else len(order))


def test_each_call_once(record):
    """Each call is listed once in the Overview's queue, once in Your call and once on the question it is filed under;
    the Overview draws no second list of them."""
    doc = example()
    t = Html(page_of(record, doc))
    subjects = [r["attrs"]["data-for"] for r in t.of_class("crow", t.by_id("your-call"))]
    assert subjects == [f"call-{c['subject']}" for c in _calls_in_queue_order(doc)] and len(subjects) == len(doc["calls"])
    calls = [r for r in t.of_class("qrow", t.by_id("queue")) if r["attrs"]["data-kind"] in ("baseline", "call")]
    hrefs = [next(e for e in t.all(r) if t.has(e, "qbtn"))["attrs"]["href"] for r in calls]
    assert hrefs == [f"#call-{r['target']}" for r in doc["home"]["queue"] if r["kind"] in ("baseline", "call")] and len(set(hrefs)) == len(hrefs)
    assert not t.of_class("callrow", t.by_id("overview"))
    for q in doc["questions"]:
        rail = t.by_id(f"q-{q['id']}:rail")
        assert [r["attrs"]["data-for"] for r in t.of_class("callrow", rail)] == [f"call-{s}" for s in q["calls"]]


def test_your_call_in_the_queues_order(record):
    """Your call holds the user's calls, each at the Decide level, in the Overview queue's order, a call on a question
    rb does not list or on none in its place; then one link to what waits for a confirmation, in rb's words; then what
    is open in the record, one line that opens its list in the inspector (P12, plain words pass 4)."""
    doc = _calls_everywhere()
    t = Html(page_of(record, doc))
    yc = t.by_id("your-call")
    assert [r["attrs"]["data-for"] for r in t.of_class("crow", yc)] == [f"call-{c['subject']}" for c in _calls_in_queue_order(doc)]
    link = next(e for e in t.all(yc) if e["tag"] == "a" and e["attrs"]["href"] == "#to-confirm")
    assert said(t.text(link)) == doc["home"]["waiting_line"]
    marks = [("calls" if t.has(e, "list") else "open" if t.has(e, "oilink") else "link") for e in t.all(yc) if t.has(e, "list") or t.has(e, "wlink") or t.has(e, "oilink")]
    assert marks == ["calls", "link", "open"]
    no_wait = copy.deepcopy(doc)
    no_wait["pending"]["rows"] = []
    assert 'href="#to-confirm"' not in page_of(record, no_wait)


def test_your_call_lines_and_what_is_open(record):
    """Your call: rb's quiet line; the calls, each with each action's line as rb gives it; what is open in the record,
    grouped by kind with rb's heading and count, the work filed under no question last, never a command: one card the
    inspector opens, which Your call's line and the Overview's line under its queue name (P12, plain words pass 4)."""
    doc = example()
    t = Html(page_of(record, doc))
    yc = t.by_id("your-call")
    lede = t.of_class("lede", yc)[0]
    assert said(t.text(lede)) == f"{doc['home']['quiet']} You make a call in your terminal; rb records it with your reason."
    lines = [said(t.text(e)) for e in t.all(yc) if e["tag"] == "pre"]
    assert lines == [a["line"] for c in _calls_in_queue_order(doc) for a in c["actions"]]
    items = t.by_id("open-items")
    # design pass 1, target 2: a checklist grouped by what is missing, each group a plain sentence with its count, each
    # item one short sentence linked to its node with its id a trailing chip; rb's own heading one click down
    heads = [said(t.text(e)) for e in t.all(items) if e["tag"] == "h4"]
    assert [g["code"] for g in doc["open_items"]] == ["late", "setting_unknown"]
    assert heads == ["Rules written after their results (1).", "Settings with no value found yet (1).", f"{doc['unfiled']['line']} (1)"]
    late = t.of_class("chk", items)[0]
    c2 = doc["findings"]["c2"]
    assert doc["open_items"][0]["items"][0]["units"] == "c2 3 of 3 units"
    assert said(t.raw(late)) == f"{c2['statement']}: all 3 of its results came first c2"
    assert [e["attrs"]["href"] for e in t.all(late) if e["tag"] == "a"] == ["#c-c2"] and said(t.text(t.of_class("cid", late)[0])) == "c2"
    raw = [said(t.text(e)) for e in t.of_class("raw", items)]
    assert raw == ["details " + ". ".join(x for x in (g["head"], g["under"]) if x) for g in doc["open_items"]]
    assert "rb " not in said(t.text(items))
    assert t.has(items, "card") and not t.inside(items, yc) and said(t.text(t.of_class("tt", items)[0])) == "Also open in the record (3)"
    assert items["attrs"]["data-crumb"] == "Also open in the record" and items["attrs"]["data-home"] == "your-call"
    line = t.of_class("oilink", yc)[0]
    assert said(t.text(line)) == "Also open in the record (3) →" and [e["attrs"]["href"] for e in t.all(line) if e["tag"] == "a"] == ["#open-items"]
    over = t.of_class("openrec", t.by_id("queue"))[0]
    assert [e["attrs"]["href"] for e in t.all(over) if e["tag"] == "a"] == ["#open-items"]


def test_unfiled_work_stays_shown(record):
    """A call on a criterion filed under no question is listed, in Your call and in the Overview's queue, and the
    open items on such work are listed under rb's line for it."""
    doc = example()
    t = Html(page_of(record, doc))
    unfiled = [c for c in doc["calls"] if c["question"] == ":unfiled"]
    assert unfiled
    assert {f"call-{c['subject']}" for c in unfiled} <= {r["attrs"]["data-for"] for r in t.of_class("crow", t.by_id("your-call"))}
    assert {f"#call-{c['subject']}" for c in unfiled} <= {e["attrs"]["href"] for e in t.of_class("qbtn", t.by_id("queue"))}
    for o in doc["unfiled"]["open_items"]:
        assert said(o["what"]) in said(t.text(t.by_id("open-items")))


# ---------------------------------------------------------------- voices, tiers, links and what is absent

AUTHOR = ["project.title", "questions[*].text", "questions[*].lead.text", "calls[*].title", "home.notes[*].text", "experiments[*].title"]


def _values(doc, spec: str) -> list:
    items = [doc]
    for part in re.findall(r"[^.\[\]]+|\[\*\]", spec):
        items = [y for x in items for y in (x if part == "[*]" else [x.get(part)]) if y is not None]
    return [x for x in items if isinstance(x, str) and x]


def test_three_voices_by_form(record):
    """Author text is drawn verbatim in an element of its own, never with rb's words inside it; rb's lines are drawn
    whole, outside author text."""
    doc = example()
    t = Html(page_of(record, doc))
    authored = [said(t.text(e)) for e in t.of_class("au")]
    for spec in AUTHOR:
        for v in _values(doc, spec):
            assert said(v) in authored, (spec, v)
    rb_lines = [doc["home"]["quiet"], doc["home"]["scope"], doc["home"]["made"]["line"], *(r["text"] for r in doc["pending"]["rows"])]
    for line in rb_lines:
        assert not any(said(line) in a for a in authored), line


# ---------------------------------------------------------------- a 390-pixel screen

def css(page: str) -> dict:
    """The page's style rules outside any @media block, and those in each block: {media: {selector: {property: value}}},
    a later rule over an earlier one ("" for no block)."""
    sheet = re.sub(r"/\*.*?\*/", "", "".join(re.findall(r"<style>(.*?)</style>", page, flags=re.S)), flags=re.S)
    rules, media, depth, i = {}, [""], 0, 0
    # rb draws its styles with no space a browser does not read: read each rule spaced, as shell.css writes it
    spaced = lambda text, gap: re.sub(gap, lambda g: g.group(0).strip() + " ", " ".join(text.split())).strip()
    for m in re.finditer(r"([^{}]*)\{([^{}]*)\}|([^{}]*)\{|\}", sheet):
        if m.group(1) is not None:
            for sel in (x.strip() for x in m.group(1).split(",")):
                sel = re.sub(r"\s*>\s*", " > ", " ".join(sel.split())).replace("( > ", "(> ")
                decl = rules.setdefault(media[-1], {}).setdefault(sel, {})
                for d in m.group(2).split(";"):
                    if ":" in d:
                        k, v = d.split(":", 1)
                        decl[k.strip()] = spaced(v, r"\s*,\s*")
        elif m.group(3) is not None:
            media.append(spaced(m.group(3), r"(?<=[a-z]):\s*"))
        elif len(media) > 1:
            media.pop()
    return rules


def test_commands_and_long_lines_keep_to_a_390_screen(record):
    """SHELL-SPEC 3.1 and rb open's own fix (test_open.py, a waiting design's command): a command is never broken
    across lines, since an id broken cannot be typed back; it scrolls inside its box, whole, on every screen, and wraps
    only on paper. The Latest changes line, one link, wraps; a design's parts keep their spaces."""
    rules = css(page_of(record))
    screen = rules[""]
    assert screen[".cmd"]["white-space"] == "pre" and screen[".cmd"]["overflow-x"] == "auto" and "overflow-wrap" not in screen[".cmd"]
    for media, sheet in rules.items():
        if media and "print" not in media:
            assert "white-space" not in sheet.get(".cmd", {}) and "white-space" not in sheet.get(".counts.latest a", {}), media
    assert screen[".counts.latest a"]["white-space"] == "normal" and screen[".counts a"]["white-space"] == "nowrap"
    assert screen[".wp"]["white-space"] == "pre-wrap"
    printed = next(sheet for media, sheet in rules.items() if "print" in media)
    assert printed[".cmd"]["white-space"] == "pre-wrap" and printed[".cmd"]["overflow-wrap"] == "anywhere"


def test_words_ids_and_values_break_only_between_them(record):
    """Look 2 item 11: at 390 a line breaks between words, never at a word's own hyphen ("ResNet-" / "50") nor inside
    one where it fits on a line of its own; the record's title is cut with an ellipsis in the top bar, never broken, and
    the record's id, which About says (plain words pass 4, one bar), breaks only where it cannot fit a line; and a run's numbers in an experiment's Runs keep each arm's part whole, its line of numbers across the
    width below the run at 390, scrolling in place where a part is wider than the screen."""
    doc = example()
    page = page_of(record, doc)
    t = Html(page)
    title = next(e for e in t.of_class("au") if said(t.text(e)) == said(doc["project"]["title"]))
    joined = [w for w in doc["project"]["title"].split() if re.search(r"\w-\w", w)]
    assert joined and [said(t.text(e)) for e in t.of_class("nw", title)] == joined
    assert not t.of_class("oid", t.by_id("top")) and doc["project"]["id"] not in t.text(t.by_id("top"))
    recid = t.of_class("recid", t.by_id("about"))[0]
    assert [said(t.text(e)) for e in t.of_class("mono", recid)] == [doc["project"]["id"]]
    rules = css(page)
    app = next(sheet for media, sheet in rules.items() if media == "@media screen")
    assert app["html.app header.top .ttl"]["text-overflow"] == "ellipsis" and app["html.app header.top .ttl"]["white-space"] == "nowrap"
    assert rules[""][".recid .mono"]["overflow-wrap"] == "break-word"
    for media, sheet in rules.items():
        if "print" not in media:
            assert not [sel for sel, decl in sheet.items() if decl.get("overflow-wrap") == "anywhere"], media
    # a run's numbers each sit in a cell of their own, kept whole (design pass 1, target 5); the table scrolls in a box
    # of its own, so at a phone's width it never runs past the screen (target 9)
    x = doc["experiments"][0]
    runs = t.by_id(f"x-{x['id']}:runs")
    nums = [e for e in t.all(runs) if e["tag"] == "td" and t.has(e, "num")]
    assert nums and t.has(t.of_class("runs2", runs)[0]["up"], "wrap")
    assert rules[""]["table.runs2 td.num"]["white-space"] == "nowrap" and rules[""][".wrap"]["overflow-x"] == "auto"


def test_a_runs_row_says_its_values_once(record):
    """Design pass 1, target 5: an experiment's runs are one table whose columns are the settings that vary among its
    runs, then each metric per arm (the baseline's first) and the difference in words; a setting every run shares is
    said once above it ("Every run: one cycle 1"), never in a column or a row (a run that gives none, a database row,
    makes it vary); numbers show two significant digits
    of their scale, the full value on hover; the run's id trails, small."""
    doc = example()
    x = doc["experiments"][0]
    t = Html(page_of(record, doc))
    sec = t.by_id(f"x-{x['id']}:runs")
    table = t.of_class("runs2", sec)[0]
    heads = [said(t.text(e)) for e in t.all(table) if e["tag"] == "th"]
    assert doc["runs"]["runsql7"]["per_run_text"] == ""
    # fix 3: each metric's title once, over its arms by their plain names and the difference, as the grid says them
    assert heads == ["Seed", "One cycle", "Top-1", "ONNX top-1", "Latency (ms)", "Produced", "Recorded", "run",
                     "FP32", "INT8", "Difference", "FP32", "INT8", "Difference", "FP32", "INT8", "Difference"], heads
    groups = [e for e in t.all(table) if e["tag"] == "th" and e["attrs"].get("scope") == "colgroup"]
    assert [(said(t.text(e)), e["attrs"]["colspan"]) for e in groups] == [("Top-1", "3"), ("ONNX top-1", "3"), ("Latency (ms)", "3")]
    assert not t.of_class("rq", sec)
    row = next(r for r in t.all(table) if r["tag"] == "tr" and r["attrs"].get("data-go") == "run-runs1")
    cells = [c for c in row["kids"] if isinstance(c, dict)]
    # a column's decimals show two significant digits of its largest difference (P5), the full value on hover
    assert [said(t.text(c)) for c in cells] == ["1", "1", "0.760", "0.757", "0.003 lower", "0.75690", "0.75660", "0.00030 lower", "", "", "", "2026-10-06", "2026-10-06", "receipt"]
    assert cells[2]["attrs"]["title"] == "0.7598" and cells[4]["attrs"]["title"] == "-0.0029"
    # the row names the run; its last cell opens its receipt, never saying its id (Look 7)
    assert not t.of_class("cid", cells[-1]) and "runs1" not in said(t.text(table)) and "one_cycle" not in said(t.text(table))
    # where every run gives the same value, it is said once above the table, not as a column
    for r in x["run_ids"]:
        doc["runs"][r]["per_run_text"] = f"seed={r[-1]} · one_cycle=1"
    t = Html(page_of(record, doc))
    sec = t.by_id(f"x-{x['id']}:runs")
    assert said(t.text(t.of_class("rq", sec)[0])) == "Every run: one cycle 1"
    assert [said(t.text(e)) for e in t.all(t.of_class("runs2", sec)[0]) if e["tag"] == "th"][:2] == ["Seed", "Top-1"]
    # a variant's key is said in plain words under its metric, never as recorded ("step_by_step")
    for r in x["run_ids"]:
        doc["runs"][r]["metrics_text"] = doc["runs"][r]["metrics_text"].replace("int8", "step_by_step")
    t = Html(page_of(record, doc))
    heads = [said(t.text(e)) for e in t.all(t.of_class("runs2", t.by_id(f"x-{x['id']}:runs"))[0]) if e["tag"] == "th"]
    assert "Step by step" in heads and not [h for h in heads if "step_by_step" in h], heads


def test_each_view_roots_the_inspector_on_its_own(record):
    """Look 2 items 5 and 6, Look 1 item 3, OVERVIEW-V2: on the Overview and on a view with no rail of its own the
    inspector's root is rb's one line, never the Overview's review list; a root with no name draws no
    empty bar on a wide screen; and a card opens in the inspector, never under the view shown."""
    doc = example()
    page = page_of(record, doc)
    t = Html(page)
    roots = {v["attrs"]["id"]: v["attrs"]["data-root"] for v in t.all() if v["tag"] in ("section", "article") and "view" in v["attrs"].get("class", "").split()}
    # OVERVIEW-V2: the Overview's inspector shows its empty state, never a second copy of the queue
    for view in ("overview", "research", "criteria", "experiments", "runs", "resources", "history"):
        assert roots[view] == "insp-none", view
    none = t.by_id("insp-none")
    assert "hidden" in none["attrs"] and said(t.text(none)) == "Pick a question, a rule or a run to see it here."
    for rail in (e for e in t.all() if "data-rail" in e["attrs"]):
        assert rail["attrs"].get("data-rootname"), rail["attrs"].get("id")
    assert not [e for e in t.all(t.by_id("overview")) if "data-rail" in e["attrs"]]
    rules = css(page)
    wide = next(sheet for media, sheet in rules.items() if "min-width: 960px" in media)
    assert wide["html.app .ibar:has(> .crumbs > .rootname:empty)"]["display"] == "none"
    app = next(sheet for media, sheet in rules.items() if media == "@media screen")
    assert app["html.app.ready main > .nodes"]["display"] == "none"


def test_a_missed_gates_row_leads_to_the_call_it_asks_for(record):
    """Each call once: a missed gate's row leads to the direction call on its experiment, as a criterion's row leads to
    the call on it, so Research's unfiled work lists no call its rows reach."""
    doc = example()
    gate = next(c for c, f in doc["findings"].items() if f.get("role") == "gate")
    eid = doc["findings"][gate]["experiment"]
    call = next(c for c in doc["calls"] if c["subject"] == eid and c["code"] == "gate_missed")
    q = next(q for q in doc["questions"] if gate in q.get("findings", []))
    t = Html(page_of(record, doc))
    row = t.by_id(f"f-{gate}:{'q-' + q['id']}") or next(e for e in t.of_class("evr") if e["attrs"]["id"].startswith(f"f-{gate}:"))
    assert [e["attrs"]["href"] for e in t.of_class("callk", row)] == [f"#call-{call['subject']}"]


def test_what_is_selected_is_a_quiet_tint(record):
    """The owner, on Look 2's navigator: the selected or current item is a quiet tint and stronger text, never a
    coloured border or inset, the same in light and dark; focus rings show for the keyboard only."""
    rules = css(page_of(record))
    for media, sheet in rules.items():
        if "print" in media or "forced-colors" in media:
            continue
        for sel, decl in sheet.items():
            if re.search(r"aria-current|\.sel\b|a\.in\b", sel):
                assert "accent" not in decl.get("box-shadow", "") and "accent" not in decl.get("border-bottom-color", ""), (media, sel, decl)
                assert "accent" not in decl.get("border-left", "") and "accent" not in decl.get("border-color", ""), (media, sel, decl)
            if ":focus" in sel and ":focus-visible" not in sel:
                assert decl.get("outline", "none") == "none", (media, sel, decl)
    app = next(sheet for media, sheet in rules.items() if media == "@media screen")
    assert app['html.app nav.nav a[aria-current="page"]'] == {"background": "var(--hover2)", "font-weight": "600"}
    for sel in ("html.app .sel > summary", "html.app .srow.sel", "html.app .pc.sel", "html.app li.evr.sel", "html.app table:not(.ev) tr.sel td"):
        assert app[sel] == {"background": "var(--hover2)", "font-weight": "500"}, sel


def test_the_theme_control_is_an_icon(record):
    """The owner: the theme control is an icon button the size of the help button, a half-filled circle, a sun or a
    moon for the theme in force, named in words on its label; its SVG holds no text; with no script it is not drawn."""
    page = page_of(record)
    t = Html(page)
    b = next(e for e in t.all() if e["tag"] == "button" and "data-theme" in e["attrs"])
    assert b["attrs"]["aria-label"] == "Theme: system" == b["attrs"]["title"] and said(t.text(b)) == ""
    svgs = [e for e in b["kids"] if isinstance(e, dict)]
    assert [e["tag"] for e in svgs] == ["svg"] * 3 and [e["attrs"]["class"] for e in svgs] == ["th th-sys", "th th-light", "th th-dark"]
    for svg in svgs:
        assert svg["attrs"]["aria-hidden"] == "true"
        assert not [e for e in t.all(svg) if e["tag"] in ("title", "style", "text", "desc")]
    rules = css(page)
    assert rules[""][".ctl"]["display"] == "none"
    app = next(sheet for media, sheet in rules.items() if media == "@media screen")
    assert app["html.app .ctl.ico"]["width"] == app["html.app .ctl"]["height"] == "28px"
    assert "data-theme" not in page_of(record, surface="export").split("<main", 1)[0].split("<header", 1)[-1]


def test_a_changed_since_line_keeps_the_users_spaces(record):
    """SPRINT-OCT24 guarantee 14: a design's changed-since line keeps the spaces the user wrote, so `ship  INT8` is
    never shown as `ship INT8`: the line is drawn as given, and its class keeps white space on screen and on paper."""
    doc = example()
    line = 'changed since it was confirmed: gate, was "ship INT8", now "ship  INT8"'
    doc["experiments"][0]["design"]["lines"] = [line]
    page = page_of(record, doc)
    drawn_lines = re.findall(r'<p class="(dline[^"]*)">([^<]*)</p>', page)
    assert ("dline", html.escape(line)) in drawn_lines, drawn_lines
    for media, sheet in css(page).items():
        if media == "" or "print" in media:
            assert sheet.get(".dline", {}).get("white-space", "pre-wrap" if media else None) in ("pre-wrap", "break-spaces"), media



def test_a_sessions_gate_on_the_experiment_card_keeps_its_spaces(record):
    """Oct 22 follow-up: on the experiment card a session's gate, `ship  INT8`, read `ship INT8`, its run of spaces
    collapsed, while the design's changed-since line above it kept them (.dline). A design a session wrote any part of
    draws its facts so they keep every space, on screen and on paper, and wrap inside their box; the user's own design draws as it did."""
    doc = example()
    x = next(e for e in doc["experiments"] if e.get("gate_parts"))
    x["gate_parts"] = [["holds", "ship  INT8"], ["not", "keep FP32"]]
    x["design"]["pending"], x["design"]["standing"] = True, "not_confirmed"
    page = page_of(record, doc)
    assert re.search(r'<dl class="facts parts">(?:(?!</dl>).)*<span class="au">ship  INT8</span>', page, re.S), page
    for media, sheet in css(page).items():
        if media == "" or "print" in media:
            # break-spaces, not pre-wrap: pre-wrap lets the space at a wrap hang past its box (app.mjs's INNER at 390)
            assert sheet.get("dl.facts.parts dd", {}).get("white-space", "break-spaces" if media else None) == "break-spaces", media
    x["design"]["pending"], x["design"]["standing"] = False, "yours"
    own = page_of(record, doc)
    assert re.search(r'<dl class="facts">(?:(?!</dl>).)*<span class="au">ship  INT8</span>', own, re.S), own

def _chrome() -> str:
    for name in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser", "chrome"):
        if shutil.which(name):
            return shutil.which(name)
    mac = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    return str(mac) if mac.exists() else ""


# drawn in a frame 390 pixels wide (a window is never narrower than 500): every fold open, then each element whose right
# edge is past the screen and that no scroll box of its own holds
_BOUNDS = """<script>
document.querySelectorAll('details').forEach(d => d.open = true);
const W = document.documentElement.clientWidth, past = [];
for (const el of document.body.querySelectorAll('*')) {
  const r = el.getBoundingClientRect();
  if (!(r.width || r.height) || r.right <= W + 0.5 || el.closest('.skip, .sr, script')) continue;
  let up = el.parentElement, held = false;
  for (; up && up !== document.body; up = up.parentElement) if (/auto|scroll|hidden/.test(getComputedStyle(up).overflowX)) { held = true; break; }
  if (!held) past.push([el.tagName.toLowerCase(), el.className, Math.round(r.right), el.textContent.trim().slice(0, 80)]);
}
parent.document.body.setAttribute('data-bounds', JSON.stringify({W: W, wide: document.documentElement.scrollWidth, past: past}));
</script>"""


def test_nothing_runs_past_a_390_screen(record, tmp_path):
    """SHELL-SPEC 3.1, checked by element bounds in headless Chrome: on a 390-pixel screen, with every fold open and no
    script of rb's, the page never scrolls sideways and no element runs past the screen, except inside a box that
    scrolls on its own (a command, a table). Design pass 1, target 9: a cited rule's card says the commit and sha256 it
    read whole (40 and 64 digits), which set the card past a 390 screen on the INT8 export until a hash line could
    break."""
    chrome = _chrome()
    if not chrome:
        pytest.skip("the bounds are measured in Chrome, and none is installed here")
    hashed = example()
    hashed["findings"]["c2"]["cited"].update(commit="1e2147ef3da5c08b635a5323db589ef98e71ddef",
                                             sha256="54fe5e466749f4dfb72fb0114d5b3ed3d5043294f890513d643ddd5e9a8226ce")
    # PIVOT: a grid of many columns scrolls inside its own box
    wide = example()
    wide["experiments"][0]["table"] = {"metrics": [{"name": "top1", "unit": "%"}], "varies": ["quant"], "conditions": ["calib_images"], "rows": [
        _row(v, "baseline" if v == "fp32" else "candidate", {"calib_images": str(c)}, _cell(76.13 - i / 10, 3, delta=None if v == "fp32" else -0.33, basis="unpaired"))
        for v in ("fp32", "int8", "w8a16") for i, c in enumerate((128, 256, 512, 1024, 2048, 4096))]}
    for n, (doc, surface) in enumerate(((example(), "page"), (_calls_everywhere(), "page"), (hashed, "export"), (wide, "page"))):
        page = tmp_path / f"p390-{n}.html"
        drawn_ = page_of(record, doc, surface).replace("</body>", _BOUNDS + "</body>")
        page.write_text('<!doctype html><html><body style="margin:0"><iframe style="width:390px;height:900px;border:0" srcdoc="'
                        + html.escape(drawn_, quote=True) + '"></iframe></body></html>', encoding="utf-8")
        r = subprocess.run([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--window-size=800,1000",
                            f"--user-data-dir={tmp_path / f'chrome-{n}'}", "--dump-dom", page.as_uri()],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        m = re.search(r'data-bounds="([^"]*)"', r.stdout)
        assert m, (r.returncode, r.stderr[-2000:])
        bounds = json.loads(html.unescape(m.group(1)))
        assert 370 <= bounds["W"] <= 390 and bounds["wide"] <= bounds["W"] and not bounds["past"], bounds


# the bar at 390, with no script: how far right it reaches, where its ? ends, and which Rerun openers show
_BAR_AT = """<script>
const W = document.documentElement.clientWidth, bar = document.querySelector('header.top');
const shown = e => !!e && getComputedStyle(e).display !== 'none' && e.getBoundingClientRect().width > 0;
const keys = document.querySelector('header.top [data-keys]');
parent.document.body.setAttribute('data-bar', JSON.stringify({W: W, wide: document.documentElement.scrollWidth,
  right: Math.max(...[...bar.querySelectorAll('*')].filter(shown).map(e => e.getBoundingClientRect().right)),
  keys: shown(keys) ? keys.getBoundingClientRect().right : null,
  bar: shown(document.querySelector('header.top a[data-rerun]')), overview: shown(document.querySelector('#reproduce a[data-rerun]'))}));
</script>"""


def test_at_390_the_bar_keeps_its_help_and_reproduce_offers_rerun(record, tmp_path):
    """A's RERUN item 2: with no script (the export as opened from disk, or the hub's frame before its script), the
    bar's Rerun this research is hidden under 640, as A's script hides it: at 390 it pushed the bar to 398 pixels and
    cut the ? button. The Overview's Reproduce place still offers it; at 1280 the bar has it too."""
    chrome = _chrome()
    if not chrome:
        pytest.skip("measured in Chrome, and none is installed here")
    doc = example()
    doc["reproduce_url"] = "https://lab.example/int8/bundle.zip"
    drawn_ = page_of(record, doc, surface="export").replace("</body>", _BAR_AT + "</body>")
    seen = {}
    for width in (390, 1280):
        page = tmp_path / f"bar{width}.html"
        page.write_text(f'<!doctype html><html><body style="margin:0"><iframe style="width:{width}px;height:844px;border:0" srcdoc="'
                        + html.escape(drawn_, quote=True) + '"></iframe></body></html>', encoding="utf-8")
        r = subprocess.run([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--window-size=1400,1000",
                            f"--user-data-dir={tmp_path / f'chrome-{width}'}", "--dump-dom", page.as_uri()],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        m = re.search(r'data-bar="([^"]*)"', r.stdout)
        assert m, (r.returncode, r.stderr[-2000:])
        seen[width] = json.loads(html.unescape(m.group(1)))
    at = seen[390]
    assert 370 <= at["W"] <= 390 and at["wide"] <= at["W"] and at["right"] <= at["W"] + 0.5, at
    assert at["keys"] is None or at["keys"] <= at["W"] + 0.5, at
    assert not at["bar"] and at["overview"], at
    assert seen[1280]["bar"] and seen[1280]["overview"], seen[1280]


# with no script of rb's: the Overview named as the target, then the Rerun panel, measured under the bar and its tabs
_RERUN_AT = """<script>
const R = e => { const b = e.getBoundingClientRect(); return {top: b.top, bottom: b.bottom, left: b.left, right: b.right, w: b.width, h: b.height}; };
const over = (a, b) => a.w && a.h && b.w && b.h && a.bottom > b.top && a.top < b.bottom && a.right > b.left && a.left < b.right;
location.hash = 'overview';
const ov = getComputedStyle(document.getElementById('overview'));
const framed = ov.outlineStyle !== 'none' && parseFloat(ov.outlineWidth) > 0;
location.hash = 'rerun';
setTimeout(() => {
  const p = document.getElementById('rerun'), pr = R(p), nav = document.querySelector('nav.nav'), top = document.querySelector('header.top');
  const tabs = [...nav.querySelectorAll('a')].filter(a => a.getBoundingClientRect().width);
  const about = nav.querySelector('.navctl a'), first = nav.querySelector('ul a');
  const close = p.querySelector('a.rrclose'), h2 = p.querySelector('h2');
  parent.document.body.setAttribute('data-rerun', JSON.stringify({W: document.documentElement.clientWidth, framed: framed,
    shown: getComputedStyle(p).display !== 'none', panel: pr, nav: R(nav), top: R(top),
    covered: tabs.filter(a => over(R(a), pr)).map(a => a.textContent.trim()),
    about: about && R(about), first: first && R(first), close: close && getComputedStyle(close).display !== 'none' ? R(close) : null,
    close_to: close && close.getAttribute('href'), h2: R(h2),
    past: [...p.querySelectorAll('.cmd')].filter(e => e.scrollWidth > e.clientWidth + 1).map(e => e.textContent.slice(0, 60)),
    copy: [...p.querySelectorAll('[data-copybtn]:empty')].map(e => e.getBoundingClientRect().height)}));
}, 300);
</script>"""


def test_the_rerun_panel_with_no_script_opens_under_the_bar_with_a_way_out(record, tmp_path):
    """Look 7, with no script of rb's (the export as opened from disk, the hub's frame before its script): the panel opens
    under the top bar and its tabs, never over History or Open calls, at 1280 and at 390; it has a Close back to the
    Overview and no empty space above its title. A section named as the target (#overview) gets no gold frame, and at
    1280 About ends the tabs' row, inside the page column."""
    chrome = _chrome()
    if not chrome:
        pytest.skip("measured in Chrome, and none is installed here")
    doc = example()
    doc["reproduce_url"] = "https://lab.example/int8/bundle.zip"
    drawn_ = re.sub(r"<script\b.*?</script>", "", page_of(record, doc, surface="export"), flags=re.S).replace("</body>", _RERUN_AT + "</body>")
    assert 'class="navctl"' in drawn_ and 'id="rerun"' in drawn_
    seen = {}
    for width in (390, 1280):
        page = tmp_path / f"rerun{width}.html"
        page.write_text(f'<!doctype html><html><body style="margin:0"><iframe style="width:{width}px;height:844px;border:0" srcdoc="'
                        + html.escape(drawn_, quote=True) + '"></iframe></body></html>', encoding="utf-8")
        r = subprocess.run([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--window-size=1400,1000",
                            "--virtual-time-budget=3000", f"--user-data-dir={tmp_path / f'chrome-{width}'}", "--dump-dom", page.as_uri()],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        m = re.search(r'data-rerun="([^"]*)"', r.stdout)
        assert m, (r.returncode, r.stderr[-2000:])
        seen[width] = json.loads(html.unescape(m.group(1)))
    for width, at in seen.items():
        assert at["shown"] and not at["framed"], (width, at)
        # under the sticky tabs, and under the top bar where it is on the screen; none of the tabs under the panel
        assert at["panel"]["top"] >= at["nav"]["bottom"] - 0.5 and (at["top"]["bottom"] <= 0 or at["panel"]["top"] >= at["top"]["bottom"] - 0.5), (width, at)
        assert not at["covered"], (width, at)
        # a way out, back to the Overview, inside the panel; the title starts at the panel's top, after its padding
        assert at["close"] and at["close_to"] == "#overview" and at["panel"]["top"] <= at["close"]["top"] <= at["panel"]["top"] + 24, (width, at)
        assert at["h2"]["top"] - at["panel"]["top"] <= 24, (width, at)
        # each command wraps inside its box ("bundle.zip" whole at 390), and an empty Copy place takes no room
        assert not at["past"] and at["copy"] and not any(at["copy"]), (width, at)
    wide = seen[1280]
    column = ((wide["W"] - 880) / 2, (wide["W"] + 880) / 2)
    assert abs(wide["about"]["top"] - wide["first"]["top"]) <= 6 and column[0] - 0.5 <= wide["about"]["left"] and wide["about"]["right"] <= column[1] + 0.5, wide


# the Overview at 390: how far below the title Needs your judgment starts, and its first row ends
_QUEUE_AT = """<script>
const t0 = document.querySelector('#h-overview').getBoundingClientRect().top;
const row = document.querySelector('#queue .qrow').getBoundingClientRect();
parent.document.body.setAttribute('data-queue', JSON.stringify({head: document.querySelector('#h-queue').getBoundingClientRect().top - t0,
  row: row.bottom - t0, W: document.documentElement.clientWidth}));
</script>"""


def test_at_390_needs_your_judgment_starts_on_the_first_screen(record, tmp_path):
    """Look 3, film pass 2: at 390 the standing card and the integrity strip stay compact (the integrity parts flow as
    one paragraph), so the queue's heading and its first row sit on the first screen under rb open's top bar and nav
    (about 155 pixels of an 844-pixel phone screen): its first row ends within 600 pixels of the title."""
    chrome = _chrome()
    if not chrome:
        pytest.skip("measured in Chrome, and none is installed here")
    page = tmp_path / "q390.html"
    drawn_ = page_of(record).replace("</body>", _QUEUE_AT + "</body>")
    page.write_text('<!doctype html><html><body style="margin:0"><iframe style="width:390px;height:844px;border:0" srcdoc="'
                    + html.escape(drawn_, quote=True) + '"></iframe></body></html>', encoding="utf-8")
    r = subprocess.run([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--window-size=800,1000",
                        f"--user-data-dir={tmp_path / 'chrome'}", "--dump-dom", page.as_uri()],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    m = re.search(r'data-queue="([^"]*)"', r.stdout)
    assert m, (r.returncode, r.stderr[-2000:])
    at = json.loads(html.unescape(m.group(1)))
    assert 370 <= at["W"] <= 390 and at["row"] <= 600, at


# what the page shows inside the hub's frame (sandbox="allow-scripts", no allow-same-origin: an opaque origin where
# storage throws), sent to the hub's side by message, since the frame cannot reach it
_HOSTED_PROBE = """<script>
const said = []; const ce = console.error; console.error = (...a) => { said.push(a.map(String).join(' ')); ce(...a); };
setTimeout(() => {
  const vis = el => !!el && !el.closest('[hidden]') && el.getBoundingClientRect().height > 0 && getComputedStyle(el).display !== 'none';
  let stored = 'kept';
  try { localStorage.getItem('x'); } catch (_) { stored = 'throws'; }
  const line = document.querySelector('#overview .ovh');
  parent.postMessage(JSON.stringify({stored: stored, origin: String(window.origin), hosted: document.documentElement.classList.contains('hosted'),
    ready: document.documentElement.classList.contains('ready'), guide: vis(document.querySelector('.guide')),
    top: vis(document.querySelector('header.top')), bar: vis(document.querySelector('header.top')) ? document.querySelector('header.top').innerText.trim() : '', theme: document.documentElement.dataset.theme || null,
    line: line ? line.textContent : '', ctl: document.querySelectorAll('#nav .navctl .ctl').length, errors: said}), '*');
}, 1500);
</script>"""
_HOST = """<!doctype html><html><body style="margin:0"><script>
addEventListener('message', e => document.body.setAttribute('data-hosted', e.data));
</script><iframe %s style="width:%dpx;height:900px;border:0" %s="%s"></iframe></body></html>"""


def _hosted(tmp_path, pages: dict, frame: bool = True) -> dict:
    """The probe's report of each page in `pages` ({name: (html, width, how)}), drawn in a sandboxed frame as the hub
    frames it, from its own address (how "src", as the hub's /read) or from srcdoc, an address that cannot be rewritten
    (frame=False: opened as it is), in a real Chrome over the DevTools protocol, each in a new profile's first visit."""
    runs = []
    for name, (page, width, how) in pages.items():
        at = tmp_path / f"{name}.html"
        if frame and how == "src":
            inner = tmp_path / f"{name}-page.html"
            inner.write_text(page.replace("</body>", _HOSTED_PROBE + "</body>"), encoding="utf-8")
            at.write_text(_HOST % ('sandbox="allow-scripts"', width, "src", inner.name), encoding="utf-8")
        elif frame:
            # "srcdoc": sandboxed, an address that cannot be rewritten; "plain": a frame on a local file, not sandboxed (a
            # null origin too, but storage kept: not the hub's)
            box = "" if how == "plain" else 'sandbox="allow-scripts"'
            at.write_text(_HOST % (box, width, "srcdoc", html.escape(page.replace("</body>", _HOSTED_PROBE + "</body>"), quote=True)), encoding="utf-8")
        else:
            probe = _HOSTED_PROBE.replace("parent.postMessage(", "document.body.setAttribute('data-hosted', ").replace("}), '*');", "}));")
            at.write_text(page.replace("</body>", probe + "</body>"), encoding="utf-8")
        runs.append((name, at.as_uri(), width))
    script = _cdp_script(tmp_path / "hosted.mjs", f"""
const out = {{}};
await cdp.run(async () => {{
  for (const [name, url, width] of {json.dumps(runs)}) {{
    const page = await (await cdp.launchChrome()).page();
    await page.viewport(width + 40, 1000);
    await page.goto(url);
    let got = null;
    for (let t = 0; t < 100 && !got; t++) {{ await cdp.sleep(100); got = await page.ev("document.body.getAttribute('data-hosted')"); }}
    out[name] = got ? JSON.parse(got) : null;
    await cdp.stopAll();
  }}
  return {{}};
}}, {{ timeoutMs: 170000 }});
console.log(JSON.stringify({{ ok: true, out }}));
""")
    code, said, err = _runner(_node(), script, env=dict(os.environ))
    assert code == 0 and said.get("ok"), err[-3000:]
    assert all(said["out"].values()), (said, err[-3000:])
    return said["out"]


def test_a_hosted_page_keeps_nothing_shows_no_guide_by_itself_and_folds_its_top_bar(record, tmp_path):
    """The hosted-mode pass (HOSTED-VIEW): the hub frames rb's page with sandbox="allow-scripts" and no
    allow-same-origin, so its origin is "null" and storage throws. There the pointer guide never shows by itself (it
    would come back on every load; the keys list still replays it), the theme follows the system's, and the page's own
    top bar folds away under the hub's: its time moves to the Overview's header line (rb's version is About's, plain words
    pass 4), its controls to the navigator; under 1280 a slim bar says the view's name alone, once the script names it.
    Outside a frame the page is as it was: the guide shows on a first visit, the bar stays."""
    page = page_of(record)
    doc = example()
    stamp = f"made {doc['at']}"
    # the export (rb's page with no script of its own) under the hub's copy of the script: the same fold, its controls
    # drawn in the navigator
    export = page_of(record, surface="export")
    script = (Path(__file__).resolve().parents[1] / "rust/src/shell/assets/script.js").read_text(encoding="utf-8")
    # the hub names the viewer on every page it serves (OWNER-READER): its meta, before its copy of the script
    meta = '<meta name="rb-viewer" content="owner">'
    export = export.replace("</head>", f"{meta}<script>{script}</script></head>", 1)
    hub = page.replace("</head>", f"{meta}</head>", 1)
    got = _hosted(tmp_path, {"wide": (hub, 1280, "src"), "narrow": (hub, 390, "src"), "srcdoc": (hub, 1280, "srcdoc"),
                             "export": (export, 1280, "src"), "plain": (page, 1280, "plain")})
    for name in ("wide", "narrow", "srcdoc"):
        g = got[name]
        assert g["stored"] == "throws" and g["origin"] == "null" and g["ready"], json.dumps(g)
        assert g["hosted"] and not g["guide"] and g["theme"] is None, json.dumps(g)
        # the theme, ? and About (design pass 1, P10) move with the folded top bar; at 390 a slim bar names the view
        assert g["line"].endswith(stamp) and g["ctl"] == 3 and f"rb {doc['rb_version']}" not in g["line"], json.dumps(g)
        assert (not g["top"]) if name != "narrow" else (not g["top"] or g["bar"] == "Overview"), json.dumps(g)
    g = got["export"]
    # About, drawn in the navigator; the theme and ? beside it once the hub's copy of the script adds them (A's part)
    assert g["hosted"] and not g["top"] and g["line"].endswith(stamp) and g["ctl"] == (3 if "addControls" in script else 1), json.dumps(g)
    # a frame on a local file has a null origin but keeps storage: not hosted, the bar stays (review, should 1)
    g = got["plain"]
    assert g["origin"] == "null" and g["stored"] == "kept" and not g["hosted"] and g["top"] and g["guide"], json.dumps(g)
    # not framed: storage kept, the guide shows by itself on a first visit, and the top bar is the page's own
    bare = _hosted(tmp_path, {"bare": (page, 1280, "")}, frame=False)["bare"]
    assert bare["stored"] == "kept" and not bare["hosted"] and bare["guide"] and bare["top"], json.dumps(bare)
    assert stamp not in bare["line"] and bare["ctl"] == 0, json.dumps(bare)


def test_plain_words_pass_1_on_the_views(record):
    """PLAIN-WORDS-PASS-1, the views' own words: the display noun is "rule" (All rules, rule met, the rule as declared),
    undetermined reads "can't tell yet" and untested "not tested", each with its hover; the tags read "rule written after
    the results" and "no interval"; the Overview's heading "How far to trust it"; "Evidence by question"; the filter
    "Filter by ID, words or state"; Runs "runs not retracted"; the composer's preview "The record will say:". rb's own
    phrase still keys colours and anchors (q1:rd:criteria-met), and no rb word for these is left on the views."""
    doc = example()
    doc["home"]["tally"] = [["meets", 3], ["undetermined", 2], ["untested", 1], ["gate undetermined", 1], ["gate untested", 2]]
    doc["findings"]["c3"]["standing"] = "criterion undetermined"
    doc["findings"]["c3"]["reading"]["result"] = "point only"
    doc["findings"]["c4"]["standing"], doc["findings"]["c4"]["role"] = "gate untested", "gate"
    page = page_of(record, doc)
    t = Html(page)
    assert said(t.text(t.by_id("h-criteria"))).startswith("Rules") and 'href="#criteria"><span class="tx">Rules</span>' in page
    assert '<h1 id="h-criteria"><span class="term" data-hint="a threshold or comparison declared for a question; commands call it a criterion.' in page
    tally = said(t.text(t.of_class("quiet", t.by_id("criteria"))[0]))
    assert tally == "Rules: 3 met · 2 can't tell yet · 1 not tested · 1 gate, can't tell yet · 2 gate not tested", tally
    assert re.search(r'<span class="term" data-hint="there are results, but they don\'t settle the rule[^"]*" aria-describedby="g-can-t-tell-yet">can(?:\'|&#x27;|&#39;)t tell yet</span>', page) or \
        re.search(r'data-hint="there are results, but they don[^"]*settle the rule[^"]*" aria-describedby="g-can-t-tell-yet">', page), "can't tell yet has its hover"
    assert 'aria-describedby="g-not-tested">not tested</span>' in page
    c3 = said(t.text(t.by_id("f-c3:q1")))
    assert "can't tell yet" in c3 and "no interval" in c3 and "point only" not in c3, c3
    assert "How far to trust it" in view_text(page, "overview") and "Evidence integrity" not in page
    assert said(t.text(t.by_id("h-research"))) == "Research" and "Evidence by question" not in page
    assert 'placeholder="Filter by ID, words or state"' in page
    assert "8 runs · 1 retracted stays on record" in view_text(page, "runs") and "not retracted" not in page and "runs stand" not in page
    assert "The record will say: " in view_text(page, "your-call") and "Its review will read" not in view_text(page, "your-call")
    assert 'href="#q-q1:rd:criteria-met"' in page
    # rb's line that leads with a rule word says it plainly: the rule as declared (a call's reason is the model's
    # drawn field, verbatim, and moves with M's pass)
    assert "<dt>as declared</dt><dd>rule met: estimate" in page and "<dd>criterion met:" not in page
    # none of rb's words for these in what the views draw themselves: state words, tallies, tags, result words,
    # headings and the nav (the cards are C's and rb's computed lines M's: each moves in its owner's pass)
    views = Html(re.sub(r'<div class="nodes">.*', "", page, flags=re.S))
    own = [e for e in views.all() if e["tag"] in ("h1", "h2") or e["attrs"].get("id") == "nav" or any(views.has(e, c) for c in ("sw", "tag", "res", "qh"))]
    assert len(own) > 20
    for old in ("criteria met", "criterion met", "criteria undetermined", "All criteria", "Retrospective", "undetermined", "untested", "point only",
                "Evidence integrity", "inquiry"):
        assert not [said(views.text(e)) for e in own if old in views.text(e)], old


def test_plain_words_pass_2_no_role_word_and_every_card_term_has_its_hover(record):
    """PLAIN-WORDS PASS 2: rb's own lines say how a call is made, never who makes it (no "the record's owner": the Open
    calls lede, the export's confirm line and the "not confirmed" hover); the rule chip on a card has the glossary's
    rule line on hover (it asks for "rule (noun)"), the card's "checks" has its line and the fold names it "checks";
    and every kept term of art the cards draw has a one-line glossary entry the fold holds."""
    doc = example()
    doc["findings"]["c1"].setdefault("says", {})["integrity"] = "ok"
    for surface in ("page", "export"):
        page = page_of(record, doc, surface)
        t = Html(page)
        # the cards' own sentence (C's nodes.rs) moves in C's pass; every line of R's says no role
        assert "record's owner" not in html.unescape(page), surface
        # "not confirmed" is said in a note's own line now (plain words pass 3); its glossary line still says no role
        assert re.search(r'<dd id="g-not-confirmed">it waits to be confirmed with rb in a terminal; until then it reads as a note', page), surface
        chips = re.findall(r'<span class="kind"><span class="term" data-hint="([^"]*)" aria-describedby="g-rule">rule</span></span>', page)
        assert chips and all(h == "a threshold or comparison declared for a question; commands call it a criterion." for h in chips), surface
        assert re.search(r'<span class="term" data-hint="problems with what a finding depends on[^"]*Not the repository(?:\'|&#x27;)s own checks\." aria-describedby="g-checks">checks</span>', page), surface
        fold = {said(t.text(e)) for e in t.all() if e["tag"] == "dt"}
        assert "checks" in fold and "integrity" not in fold and "rule (noun)" not in fold, surface
        # the glossary holds what every surface may say: no "your call" (the export says no "you" of a call) and
        # none of the words rb's screens ban (the lock is "locked", never the freeze)
        assert "your call" not in fold and not words_problems(page), (surface, words_problems(page)[:3])
        for kept in ("gain", "loss", "unresolved", "reproduced", "not reproduced", "paired difference", "cited", "margin", "question link",
                     "identity", "amended", "locked", "rb's interval", "units", "timing", "result"):
            assert t.by_id("g-" + re.sub(r"[^a-z0-9]+", "-", kept.lower()).strip("-")) is not None, (surface, kept)
    export = page_of(record, doc, "export")
    # plain words pass 4: "rb computes; it never decides" is said once, in About, and no view repeats it
    assert "Calls are made with rb in a terminal, never on this page." in export and export.count("rb computes; it never decides.") == 1
    assert "it is confirmed with rb in a terminal:" in export or "Waiting for a confirmation" not in export


def test_no_place_on_a_page(record):
    """A page names no place: given a document that carries rb open's places anyway, rb page and rb export draw none
    of them, no path, no worktree and no place line."""
    doc = example()
    doc["places"] = example("live")["places"]
    for surface in ("page", "export"):
        page = page_of(record, doc, surface)
        for leak in ("/home/ana", "~/int8-resnet50", "exp-b", ".worktrees", "record files not committed"):
            assert leak not in page, (surface, leak)


def test_reproduce_this_project_only_with_an_address(record):
    """An export made with --url carries Reproduce this project on its Overview, with the one button that opens Rerun
    this research, and the bar carries the same button; the panel says the prompt, the clone command for that address
    and the code line once for the page. Without one, there is no Reproduce, no Rerun and no https:// on the page; rb
    page draws neither, whatever its document holds (REPRODUCE-WORDS.md)."""
    page = page_of(record, surface="export")
    assert "Reproduce this project" not in page and "https://" not in page and "Rerun this research" not in page
    doc = example()
    doc["reproduce_url"] = "https://lab.example/int8/bundle.zip"
    page = page_of(record, doc, surface="export")
    t = Html(page)
    rep = view_text(page, "reproduce")
    assert rep.startswith("Reproduce this project") and rep.endswith("Rerun this research") and "rb clone" not in rep
    buttons = [e for e in t.all(t.root) if e["tag"] == "a" and "data-rerun" in e["attrs"]]
    assert [b["attrs"]["href"] for b in buttons] == ["#rerun", "#rerun"] and all(said(t.text(b)) == "Rerun this research" for b in buttons)
    assert t.inside(buttons[0], t.of_class("end", t.by_id("top"))[0]) and t.inside(buttons[1], t.by_id("reproduce"))
    panel = view_text(page, "rerun")
    # the page by the one address the reader has (the rb-page meta's: the folder --url gave); by hand, the prompt's steps
    assert "rb clone https://lab.example/int8/bundle.zip int8-resnet50" in panel and "Its record: https://lab.example/int8/" in panel and "page.html" not in panel
    assert "cd int8-resnet50 && rb context" in panel
    assert "pip install rabbit-brain" in panel and "The clone carries the structure, inherited" in panel
    assert "\u2014" not in page
    # rb page, read on the owner's own disk, has no clone address to offer
    assert "Rerun this research" not in page_of(record, doc)
    # inside the hub (its rb-viewer meta), shown only where its rb-clone meta lets readers rerun it
    hidden = [sel for sel, decl in css(page)[""].items() if sel.endswith(":not(:has(meta[name=rb-clone])) .rrun")]
    assert hidden and css(page)[""][hidden[0]]["display"].startswith("none") and "rb-viewer" in hidden[0], hidden


def _cli(spec: dict, words: list) -> tuple:
    """(the subcommand's spec, how many words name it) for `rb <words...>` as rb's CLI reads it (cli_spec.json)."""
    at, n = spec, 0
    while n < len(words):
        parsers = dict(p for a in at["actions"] if a.get("kind") == "parsers" for p in a["parsers"])
        if words[n] not in parsers:
            break
        at, n = parsers[words[n]], n + 1
    return at, n


# P's clone.patch adds rb clone --code at the merge point that takes this round; until it is in this tree, the test that
# the prompt names only what rb accepts expects that one flag alone to be missing, and says so as an expected failure
_SPEC = json.loads((Path(__file__).resolve().parent.parent / "rust" / "src" / "cli_spec.json").read_text(encoding="utf-8"))
_HAS_CODE = "--code" in [o for a in _cli(_SPEC, ["clone"])[0]["actions"] for o in a.get("options", [])]


def test_the_rerun_prompt_names_only_what_rbs_cli_accepts(record):
    """RERUN (Oct 26 19:55 +08): every rb command the prompt names, and every flag after it, is one rb's CLI accepts,
    read from cli_spec.json, so the words can never name a flag rb lacks: the whole record's prompt and an experiment's
    own, as the export draws them."""
    doc = example()
    doc["reproduce_url"] = "https://lab.example/int8/bundle.zip"
    page = page_of(record, doc, surface="export")
    eid = doc["experiments"][0]["id"]
    prompts = [view_text(page, "rerun-prompt"), view_text(page, f"x-{eid}:prompt")]
    lacks = []
    for prompt in prompts:
        named, flags = set(), set()
        # each "rb <command>" in the prompt, and the flags that follow it until the next one
        parts = re.split(r"\brb (?=[a-z])", prompt)
        for part in parts[1:]:
            words = part.split()
            sub, n = _cli(_SPEC, [w.strip(".,;:") for w in words])
            assert n, part[:60]
            named.add(" ".join(words[:n]).strip(".,;:"))
            options = {o for a in sub["actions"] for o in a.get("options", [])}
            for f in re.findall(r"(?<!\S)(--[a-z][a-z-]*)", part):
                if f not in options:
                    lacks.append((" ".join(words[:n]).strip(".,;:"), f))
                flags.add(f)
            # "rb journal run -- <command>": the command after -- is one rb takes whole
            if words[n:n + 1] == ["--"]:
                assert any(a.get("nargs") == "..." and not a.get("options") for a in sub["actions"]), (words[:n], "takes no command after --")
        assert {"clone", "context", "journal run", "reconcile", "run add", "status"} <= named, named
        # rb reconcile names the arm where a run states it (55dc6c9), so the prompt asks for no --variant
        assert "--code" in flags and "--variant" not in flags, flags
    # every flag is rb's; until clone.patch lands, rb clone's --code alone is not, and the test says so
    if _HAS_CODE:
        assert not lacks, lacks
    else:
        assert set(lacks) == {("clone", "--code")}, lacks
        pytest.xfail("rb clone --code comes with P's clone.patch at this round's merge point")


def test_a_hub_page_with_no_rb_clone_hides_only_the_rerun_parts(record):
    """A's RERUN item 1: the hub hides Rerun this research where no rb-clone meta lets readers rerun it, by a class of
    the feature's own: never `rr`, which shell.css lays out as a run's row, so a hub page with no rb-clone keeps its run
    rows. Every part of the feature carries that class, and no other element on the page, and no other rule styles it."""
    doc = example()
    doc["reproduce_url"] = "https://lab.example/int8/bundle.zip"
    page = page_of(record, doc, surface="export")
    sheet = css(page)
    hidden = [sel for sel, decl in sheet[""].items() if sel.endswith(":not(:has(meta[name=rb-clone])) " + sel.split()[-1])
              and "rb-viewer" in sel and decl.get("display", "").startswith("none")]
    assert len(hidden) == 1, hidden
    cls = hidden[0].split()[-1]
    assert cls.startswith(".") and re.fullmatch(r"\.[a-z-]+", cls), cls
    # no other rule in any block names the class: it is no run row's, nor anything else's
    others = [(m, sel) for m, rules in sheet.items() for sel in rules if re.search(re.escape(cls) + r"(?![a-z-])", sel) and sel != hidden[0]]
    assert not others, others
    t = Html(page)
    carriers = [e for e in t.all(t.root) if cls[1:] in e["attrs"].get("class", "").split()]
    eid = doc["experiments"][0]["id"]
    parts = [t.by_id("rerun"), t.by_id("reproduce")] + [e for e in t.all(t.root) if e["tag"] == "a" and "data-rerun" in e["attrs"]]
    prompts = [e["up"] for e in t.all(t.root) if re.fullmatch(r"x-.+:prompt", e["attrs"].get("id", ""))]
    assert any(e["attrs"].get("id") == f"x-{eid}:prompt" for p in prompts for e in p["kids"]) and all(p is not None for p in parts)
    parts += prompts
    assert sorted(map(id, carriers)) == sorted(map(id, parts)), [c["attrs"] for c in carriers]


def test_green_only_for_a_sound_met_rule(record):
    """A met word counting a finding that is not drawn green (rb's muted list) is drawn dim, on the tally and on a
    card's evidence line; a sound one is green: Research's card and tally, All criteria's tally, and the question's own
    line and the Overview's standing card, each linked to its reading; each tally led by what it counts ("Rules: 3
    met", plain words pass 3)."""
    page = page_of(record)
    assert page.count('<span class="k">Rules: </span><span class="sw good">3 met</span>') == 1                  # Rules' tally
    assert len(re.findall(r'<span class="k">Rules: </span><a class="sw good" href="#q-q1:rd:criteria-met">3 met</a>', page)) == 3  # both cards, the question
    doc = example()
    doc["home"]["muted"] = ["meets"]
    doc["home"]["tree"][0]["muted"] = ["meets"]
    for st in doc["home"]["standing"]:
        st["muted"] = ["meets"]
    page = page_of(record, doc)
    assert '<span class="sw good">3 met' not in page and 'class="sw good" href="#q-q1:rd' not in page and "3 met" in page


def test_numbers_in_rbs_digits(record):
    """A reason's numbers are rb's digits, and a number keeps its tolerance on its line."""
    page = page_of(record)
    assert "estimate 0.7597 outside 0.7613 ± 0.001" in page


def test_absent_fields_draw_nothing(record):
    """A document holding only what rb computes today (no phase merged beyond it) draws without error and fills in no
    word: no count, no lead and no Your call number it was not given, and no "No recorded interpretation yet." where
    interpretations exist."""
    shape = {}
    for line in (FIX / "view-1.shape.tsv").read_text(encoding="utf-8").splitlines()[1:]:
        path, _, phase, _, _ = line.split("\t")
        shape[path] = phase
    doc = example()

    def prune(v, p=""):
        if isinstance(v, dict):
            for k in list(v):
                q = f"{p}.{k}" if p else k
                q = q if q in shape else (f"{p}.{{id}}" if p else k)
                if shape.get(q, "today") not in ("today", "c"):
                    del v[k]
                else:
                    prune(v[k], q)
        elif isinstance(v, list):
            for x in v:
                prune(x, p + "[]")
    prune(doc)
    assert "calls" not in doc and "lead" not in doc["questions"][0] and "counts" not in doc["home"]
    page = page_of(record, doc)
    t = Html(page)
    assert said(t.text(next(e for e in t.all(t.by_id("nav")) if e["tag"] == "a" and e["attrs"]["href"] == "#your-call"))) == "Your call"
    ov = said(t.text(t.by_id("overview")))
    assert "No recorded interpretation yet." not in ov
    assert doc["questions"][0]["overview"]["headline"] in ov


# ---------------------------------------------------------------- R3: Research, a question, All criteria, the glossary

def _ids(t: Html) -> set:
    return {e["attrs"]["id"] for e in t.all() if "id" in e["attrs"]}


def test_research_holds_the_cards_and_unfiled_work(record):
    """Research draws each question's card as the Overview draws it (design pass 1, target 1: the same card, with no
    extra chain of tags and counts), a sub-question's set in under its parent, then the work filed under no question
    under rb's line for it (its criteria as evidence rows, the calls on them led by their reason, what is open on
    them); the rules' tally is Rules' own, not repeated here."""
    doc = example()
    t = Html(page_of(record, doc))
    res = t.by_id("research")
    cards = [e for e in t.all(res) if t.has(e, "stand")]
    assert [c["attrs"]["data-go"] for c in cards] == [f"q-{q['id']}" for q in doc["questions"]]
    assert [t.has(c, "d1") for c in cards] == [q.get("depth", 0) > 0 for q in doc["questions"]]
    # the top question's card reads word for word as the Overview's
    assert said(t.text(cards[0])) == said(t.text(t.by_id("st-q1")))
    assert said(doc["home"]["standing"][0]["interpretation"]["text"]) in said(t.text(res))
    for chain in ("no interval ·", "linked here:", "below it:", "Uncertainty remains"):
        assert chain not in said(t.text(cards[0])), chain
    unfiled = t.by_id("unfiled")
    assert unfiled is not None and t.inside(unfiled, res)
    assert said(t.text(t.by_id("h-unfiled"))) == doc["unfiled"]["line"]
    assert [r["attrs"]["id"] for r in t.of_class("evr", unfiled)] == [f"f-{c}:unfiled" for c in doc["unfiled"]["criteria"]]
    # each call once: the call on an evidence row is that row's Your call link, never a call row again under it
    assert [r["attrs"]["data-for"] for r in t.of_class("callrow", unfiled)] == []
    for c in doc["unfiled"]["calls"]:
        row = t.by_id(f"f-{c}:unfiled")
        assert [e["attrs"]["href"] for e in t.of_class("callk", row)] == [f"#call-{c}"]
    for o in doc["unfiled"]["open_items"]:
        assert said(o["what"]) in said(t.text(unfiled))
    assert not [e for e in t.all(res) if t.has(e, "quiet")]
    empty = copy.deepcopy(doc)
    empty["unfiled"] = {"line": "Work not filed under a question", "criteria": [], "calls": [], "open_items": []}
    assert Html(page_of(record, empty)).by_id("unfiled") is None


def test_an_interpretation_group_holds_its_links_when_and_why_once(record):
    """SHELL-SPEC section 16's first saving: a row in an interpretation group leaves the question link out of its
    disclosure, since "when and why" above the group holds it; a row in a group with no "when and why", or in no
    group, keeps its link, so what the link says is always on the page once."""
    doc = example()
    q = next(q for q in doc["questions"] if q["id"] == "q1")
    ip = next(ip for ip in q["overview"]["interpretations"] if ip.get("declared"))

    def links(page: str) -> dict:
        t = Html(page)
        out = {}
        for cid in doc["findings"]:
            row = t.by_id(f"f-{cid}:q1")
            if row is not None:
                out[cid] = [said(t.text(e)) for e in t.all(t.of_class("evm", row)[0]) if e["tag"] == "dt"].count("question link")
        return out

    page = page_of(record, doc)
    held = links(page)
    assert ip["criteria"] and all(held[c] == 0 for c in ip["criteria"] if c in held)
    assert any(n == 1 for c, n in held.items() if c not in ip["criteria"])
    t = Html(page)
    whens = [said(t.text(e)) for e in t.all(t.by_id("q-q1")) if e["tag"] == "details" and t.has(e, "ipm")]
    assert any(said(ip["declared"]) in w for w in whens)
    ip["declared"] = ""                               # no "when and why" above: each row says its own link again
    again = links(page_of(record, doc))
    assert all(again[c] == 1 for c in ip["criteria"] if c in again)


def test_question_rows_meaning_first(record):
    """A question's evidence row is compact (design pass 1, P2, P3, P5, P6, P8): its statement and rb's state word,
    then rb's reading as one plain sentence ("int8 is 0.0029 lower than the fp32 baseline on top1, -0.0046 to
    -0.0012, 3 seeds."), the one timing tag only where the row is the exception, and the id trailing, small; the units
    it rests on and a miss's reason, the role, the rule as declared, rb's comparison, timing and the question link sit in
    the row's disclosure, never on its lines."""
    doc = example()
    t = Html(page_of(record, doc))
    q = t.by_id("q-q1")
    row = t.by_id("f-c1:q1")
    assert row is not None and t.inside(row, q) and "data-row" in row["attrs"]
    f = doc["findings"]["c1"]
    head, value, fold = [k for k in row["kids"] if isinstance(k, dict)]
    units = [e for e in t.of_class("evu", fold) if not t.has(e, "rbr")][0]
    assert t.has(head, "evh") and said(t.text(head)) == f"{f['statement']} {f['standing'].replace('criterion met', 'rule met')}"
    assert said(t.text(t.of_class("au", head)[0])) == f["statement"]
    assert f["reading"] == {**f["reading"], "estimate": -0.0029, "lo": -0.0046, "hi": -0.0012, "n": 3, "unit": "seed", "unit_text": "top1"}
    assert said(t.text(value)) == "INT8 is 0.0029 lower than the FP32 baseline (-0.0046 to -0.0012) on Top-1, 3 seeds. c1"
    ids = t.of_class("cid", value)
    assert [said(t.text(e)) for e in ids] == ["c1"] and value["kids"][-1] is ids[0]
    assert said(t.text(units)).startswith(f["units_line"])
    assert said(t.text(t.of_class("rbr", fold)[0])) == f"{said(f['reading_short'])} · loss"
    assert fold["tag"] == "details" and t.has(fold, "evm")
    lines = said(" ".join(t.text(k) for k in (head, value)))
    # with no number in rb's reading, rb's short reading stands
    bare = copy.deepcopy(doc)
    bare["findings"]["c1"]["reading"]["estimate"] = None
    tb = Html(page_of(record, bare))
    plain = [k for k in tb.by_id("f-c1:q1")["kids"] if isinstance(k, dict)][1]
    assert said(tb.text(plain)) == f"{said(f['reading_short'])} c1"
    for machinery in ("primary", f["rule"], f["says"]["comparison"], f["says"]["timing"], f["units_line"], f["reading"]["interval_text"]):
        assert machinery not in lines and said(machinery) in said(t.text(fold)), machinery
    dts = [said(t.text(e)) for e in t.all(fold) if e["tag"] == "dt"]
    assert dts[:5] == ["rule", "as declared", "reading", "units", "timing"]
    # c1 sits in an interpretation group whose "when and why" holds the link; a row in no such group keeps its link
    assert "question link" not in dts
    outside = t.by_id("f-c2:q1")
    assert "question link" in [said(t.text(e)) for e in t.all(t.of_class("evm", outside)[0]) if e["tag"] == "dt"]
    miss = t.by_id("f-c2:q1")
    assert said(doc["findings"]["c2"]["rule_detail"]) in said(t.text([e for e in t.of_class("evu", miss) if not t.has(e, "rbr")][0]))
    shown = said(" ".join(t.text(k) for k in miss["kids"] if isinstance(k, dict) and k["tag"] != "details"))
    assert said(doc["findings"]["c2"]["rule_detail"]) not in shown
    # P6: a rule written after its results carries the tag only where most were written before theirs; where most
    # were written after, the count heads the list and only a rule written before carries a tag
    late = [c for c in doc["questions"][0]["findings"] if "retrospective" in doc["findings"][c]["tags"]]
    tested = [c for c in doc["questions"][0]["findings"] if doc["findings"][c]["state"] != "untested"]
    assert 0 < len(late) * 2 <= len(tested)
    for c in doc["questions"][0]["findings"]:
        v = [k for k in t.by_id(f"f-{c}:q1")["kids"] if isinstance(k, dict)][1]
        tags = [said(t.text(e)) for e in t.of_class("tag", v)]
        assert tags == (["rule written after the results"] if c in late else []), (c, tags)
    for c in doc["findings"]:
        doc["findings"][c]["tags"] = ["retrospective"] if c != "c1" else []
    t = Html(page_of(record, doc))
    v = [k for k in t.by_id("f-c1:q1")["kids"] if isinstance(k, dict)][1]
    assert [said(t.text(e)) for e in t.of_class("tag", v)] == ["written before its results"]
    assert [e["attrs"]["aria-describedby"] for e in t.all(v) if t.has(e, "term")] == ["g-rule-written-before-the-results"]
    v = [k for k in t.by_id("f-c2:q1")["kids"] if isinstance(k, dict)][1]
    assert not t.of_class("tag", v)
    head = t.of_class("late", t.by_id("q-q1"))
    assert head and re.fullmatch(r"(?:All \d+|\d+ of \d+) rules were written after their results came in", said(t.text(head[0])))


def test_an_unconfirmed_interpretation_reads_as_a_note(record):
    """D3 and PLAIN-WORDS-PASS-3 on the question view: an interpretation is a note; one a session wrote and the user has
    not confirmed says so in its line ("A note, not confirmed: <its words>."), never with a free-floating tag; a
    confirmed one reads "Note: <its words>." with no mark."""
    doc = example()
    t = Html(page_of(record, doc))
    unconfirmed = t.by_id("q-q1a:ip:1")
    said_ = next(e for e in t.all(unconfirmed) if e["tag"] == "h3")
    words = doc["questions"][1]["overview"]["interpretations"][0]["text"]
    assert t.has(said_, "note") and said(t.text(said_)).startswith("A note, not confirmed: " + words.rstrip("."))
    assert said(t.text(t.of_class("au", said_)[0])) == words
    assert not t.of_class("tag", unconfirmed)
    confirmed = t.by_id("q-q1:ip:1")
    head = next(e for e in t.all(confirmed) if e["tag"] == "h3")
    # the interpretation carries no mark; a call beside it is rb's line for the call, whose session note says its own
    calls = [said(t.text(e)) for e in t.of_class("ipcall", confirmed)]
    rest = said(t.text(confirmed))
    for c in calls:
        rest = rest.replace(c, "")
    assert said(t.text(head)).startswith("Note: ") and "not confirmed" not in rest and calls
    flipped = copy.deepcopy(doc)
    flipped["questions"][0]["overview"]["interpretations"][0]["confirmed"] = False
    t = Html(page_of(record, flipped))
    assert said(t.text(next(e for e in t.all(t.by_id("q-q1:ip:1")) if e["tag"] == "h3"))).startswith("A note, not confirmed: ")


def test_each_call_once_on_a_question(record):
    """On a question's view each call shows once: a waiting call in its rail (a row's disclosure links to it and does
    not repeat it), a call made on its work in rb's line under Calls made, with a session's note in it drawn as its
    words; a call filed under another question is not drawn here."""
    doc = example()
    t = Html(page_of(record, doc))
    q = t.by_id("q-q1")
    text_ = said(t.text(q))
    for m in doc["made_calls"]:
        assert text_.count(said(m["line"])) == (1 if m["question"] == "q1" else 0), m["line"]
    rail = t.by_id("q-q1:rail")
    assert [r["attrs"]["data-for"] for r in t.of_class("callrow", rail)] == ["call-c2", "call-c3"]
    # a call said beside its interpretation is said there alone, a session's note in it drawn as its words
    note = next(m for m in doc["made_calls"] if m["note"])
    group = next(g for g in t.of_class("igroup", q) if note["line"] in said(t.text(g)))
    assert note["note"] in [said(t.text(e)) for e in t.of_class("au", group)]
    assert note["line"] not in said(t.text(rail))
    assert "call-e2" not in [r["attrs"].get("data-for") for r in t.all(q)]


def test_all_criteria_lists_each_criterion_once_by_experiment(record):
    """All criteria: its count, rb's tally, then every criterion once, grouped by experiment in rb's order, each a row
    with its id, statement, reading and state folding rb's machinery; every link to a criterion lands on its row while
    no card is drawn."""
    doc = example()
    t = Html(page_of(record, doc))
    crit = t.by_id("criteria")
    assert said(t.text(t.by_id("h-criteria"))) == f"Rules ({doc['home']['counts']['criteria']})"
    rows = t.of_class("krow", crit)
    assert sorted(r["attrs"]["data-for"] for r in rows) == sorted(f"c-{c}" for c in doc["findings"])
    groups = [g for g in t.all(crit) if "data-group" in g["attrs"]]
    assert [[r["attrs"]["data-for"][2:] for r in t.of_class("krow", g)] for g in groups] == [x["findings"] for x in doc["experiments"]]
    for r in rows:
        cid = r["attrs"]["data-for"][2:]
        summary = r["kids"][0]
        assert summary["tag"] == "summary" and summary["attrs"]["id"] == f"cr-{cid}"
        # its sentence first and its id last (design pass 1, target 3)
        assert said(t.text(summary)).startswith(doc["findings"][cid]["statement"]) and said(t.text(summary)).endswith(f" {cid}")
        assert not [e for e in t.all(summary) if e["tag"] == "a"], "a summary holds no link"
    links = {e["attrs"]["href"] for e in t.all() if e["tag"] == "a"}
    # a link lands on the criterion's card where the page draws one (C's cards region), else on its row
    to = "c-c1" if t.by_id("c-c1") is not None else "cr-c1"
    assert f"#{to}" in links and not ({"#c-c1", "#cr-c1"} - {f"#{to}"}) & links


def test_the_glossary_describes_each_term(record):
    """Every term drawn with rb's hint names its glossary entry, and the glossary fold, open in the About view (design
    pass 1, P10), holds each line once, so the document has them with no script."""
    t = Html(page_of(record))
    words = t.by_id("words")
    assert words is not None and words["up"]["tag"] == "details" and "open" in words["up"]["attrs"] and t.inside(words, t.by_id("about"))
    entries = {e["attrs"]["id"]: said(t.text(e)) for e in t.all(words) if e["tag"] == "dd"}
    terms = [e for e in t.all() if t.has(e, "term")]
    assert terms
    for e in terms:
        assert entries.get(e["attrs"].get("aria-describedby")) == said(e["attrs"]["data-hint"]), e["attrs"]
    assert "g-open-question" in entries and "g-rb-s-interval" in entries and "g-not-confirmed" in entries and len(entries) == 40
    q = t.by_id("q-q1")
    assert {e["attrs"]["aria-describedby"] for e in t.all(q) if t.has(e, "term")} >= {"g-open-question", "g-rule-written-after-the-results"}
    # rb's headline carries its hint where it leads a question's evidence: a sub-question's (a top-level one's view
    # leads with rb's line on why, contract 1.6's standing)
    assert "g-headline" in {e["attrs"]["aria-describedby"] for e in t.all(t.by_id("q-q1a")) if t.has(e, "term")}


def test_no_verdict_sentence(record):
    """Where a question's answer would be, the page draws only the record's words (an interpretation, as written),
    rb's computed comparison or rb's "No recorded interpretation yet.": never an answer of the renderer's own."""
    doc = example()
    doc["questions"][1]["overview"]["interpretations"] = []
    doc["questions"][1]["lead"] = None
    t = Html(page_of(record, doc))
    for q in doc["questions"]:
        view = t.by_id(f"q-{q['id']}")
        for e in t.all(view):
            if t.has(e, "said"):
                author = t.of_class("au", e)
                rest = said(t.text(e))
                if author:
                    rest = rest.replace(said(t.text(author[0])), "").strip()
                # a note's label, then whether its rule holds as a sentence of rb's (plain words pass 3)
                assert rest == "No note on these rules yet." or re.fullmatch(r"(?:Note:|A note, not confirmed:)?\s*(?:Its (?:rule|\d+ rules) (?:is|are) (?:not )?met\.|rb can't tell yet whether its rule is met\.|Its rule has no results yet\.)?", rest), rest
    assert said(t.text(t.by_id("q-q1a:evidence"))) and "No note on these rules yet." in said(t.text(t.by_id("q-q1a")))


def test_counts_count_what_was_recorded(record):
    """A count on a question's view counts the rows it heads: "Its rules (N)" is the criteria linked to the
    question that the record holds, each drawn as a row once."""
    doc = example()
    doc["questions"][0]["findings"].append("c99")   # a criterion the record does not hold is neither counted nor drawn
    t = Html(page_of(record, doc))
    for q in doc["questions"]:
        held = [c for c in q["findings"] if c in doc["findings"]]
        head = t.by_id(f"q-{q['id']}:evidence")
        assert said(t.text(head)) == f"Its rules ({len(held)})"
        rows = [r["attrs"]["id"] for r in t.of_class("evr", t.by_id(f"q-{q['id']}"))]
        assert sorted(rows) == sorted(f"f-{c}:{q['id']}" for c in held)


def test_qualifications_and_readings_land_in_their_folds(record):
    """Each qualification on a question's evidence line links to its place in "Which comparisons carry these
    qualifications?", with what it means and the criteria that carry it; each count of a reading links to its group in
    "By reading"; the Overview's card links land there too."""
    doc = example()
    t = Html(page_of(record, doc))
    q = t.by_id("q-q1")
    for i, lim in enumerate(doc["questions"][0]["overview"]["limits"]):
        held = t.by_id(f"q-q1:lim:{i}")
        assert held is not None and t.inside(held, q) and held["up"]["tag"] == "details"
        assert [e["attrs"]["href"] for e in t.all(held) if e["tag"] == "a"] == [f"#f-{c}:q1" for c in lim["criteria"]]
        # design pass 1, target 1: no card or evidence line chains its qualifications; the question's fold holds them
        assert sum(1 for e in t.all() if e["attrs"].get("href") == f"#q-q1:lim:{i}") == 0
    met = t.by_id("q-q1:rd:criteria-met")
    assert met["up"]["tag"] == "details"
    assert sorted(e["attrs"]["href"] for e in t.all(met) if e["tag"] == "a") == ["#f-c1:q1", "#f-c3:q1", "#f-c4:q1"]
    assert t.by_id("q-q1:rd:not-reproduced") is not None


def test_a_comparisons_declarations_say_how_it_was_declared_and_never_who_or_the_users_words(record):
    """A computed comparison folds each declaration it rests on, as declared (its kind, orientation and why), under
    "Declaration and scope": never who declared it, and on rb page and rb export never the words given with it."""
    doc = example()
    q = doc["questions"][0]
    q["overview"]["relations"] = [{"summary": "The measured direction reverses; repeatability remains uncertain.", "outcome": "reversal",
                                   "kind": "reverses", "comparisons": [{"source": "c1", "target": "c3"}],
                                   "declarations": [{"kind": "reverses", "orientation": "same", "why": "Align the budgets",
                                                     "words": "PRIVATE_USER_WORDS", "declared_by": "human:ana", "session": "s-1", "via": "cli"}]}]
    for surface in ("page", "export"):
        page = page_of(record, doc, surface=surface)
        t = Html(page)
        fold = next(e for e in t.all(t.by_id("q-q1:rel")) if e["tag"] == "details")
        said_ = said(t.text(fold))
        assert said_.startswith("Declaration and scope") and "reverses" in said_ and "same" in said_ and "Align the budgets" in said_, said_
        assert "PRIVATE_USER_WORDS" not in page and "human:ana" not in page and "s-1" not in said_, surface


def test_a_computed_comparison_reads_in_rbs_voice_on_its_question(record):
    """A question with rb's computed comparison draws rb's sentence as its answer, in rb's voice, then each pair of
    findings with rb's word between them and rb's line on their order; no recorded-interpretation fallback line."""
    doc = example()
    q = doc["questions"][0]
    q["overview"]["relations"] = [{"summary": "The measured direction reverses; repeatability remains uncertain.", "outcome": "reversal",
                                   "kind": "reverses", "comparisons": [{"source": "c1", "target": "c3"}]}]
    t = Html(page_of(record, doc))
    rel = t.by_id("q-q1:rel")
    head = next(e for e in t.all(rel) if e["tag"] == "h2")
    assert said(t.text(head)) == q["overview"]["relations"][0]["summary"] and not t.of_class("au", head)
    pair = t.of_class("pair", rel)[0]
    assert [c["attrs"]["data-go"] for c in t.of_class("pc", pair)] == [f"c-{c}" if t.by_id(f"c-{c}") is not None else f"cr-{c}" for c in ("c1", "c3")]
    assert said(t.text(t.of_class("pv", pair)[0])) == "reverses"
    assert "Directions are aligned to the same comparison order." in said(t.text(rel))


@pytest.mark.parametrize("waits", ["calls", "nothing"])
def test_nothing_waits_here_only_while_nothing_waits(record, waits):
    """A question with no call says "Nothing waits on you here." only while nothing at all waits on the page: no call,
    no found item and nothing waiting for a confirmation."""
    doc = example()
    for q in doc["questions"]:
        q["calls"] = []
    if waits == "nothing":
        doc["calls"], doc["your_call"]["n"], doc["unfiled"]["calls"] = [], 0, []
        doc["home"]["found"], doc["home"]["found_total"], doc["pending"]["rows"] = [], 0, []
    t = Html(page_of(record, doc))
    rail = said(t.text(t.by_id("q-q1:rail")))
    # while something waits elsewhere, the rail opens on what is behind the question: no empty "Your call (0)" box
    assert rail.startswith("Your call (0)") == (waits == "nothing")
    assert rail.startswith("Experiments behind this question") == (waits == "calls")
    assert ("Nothing waits on you here." in rail) == (waits == "nothing")


def test_an_empty_question_says_so(record):
    """Plain words pass 4: a question with nothing linked to it says one thing under its status, "No rule tests this
    question yet.", never a headline and a box that say it again, and links to no other view's notes; where the viewer
    may act (rb page, the export's owner and a file with no viewer) the command rb gives for it is its one copy line, a
    reader reads the sentence alone; the notes that name it are listed under their own heading; its rail is headed by
    the question, since no call waits there; and it still lists the experiments behind it. No tallies beside the
    sentence (a top-level question's count its sub-questions' rules), and rb's standing line does not replace it."""
    doc = example()
    q = doc["questions"][1]
    q["findings"], q["overview"]["evidence_count"], q["calls"] = [], 0, []
    do = 'rb experiment add "<what would test it>" -q q1a --hypothesis "<what you expect>"'
    doc["open_items"].append({"code": "open_question", "head": "Questions with nothing filed under them", "n": 1, "under": "",
                              "items": [{"subject": "q1a", "code": "open_question", "what": "q1a is open and nothing is filed under it", "units": "", "criteria": [], "do": do}]})
    q["cited_by"] = [{"kind": "note", "id": "n1"}, {"kind": "note", "id": "", "text": "q1a has no rule yet: one seed at 512 images."}]
    for surface in ("page", "export"):
        page = page_of(record, doc, surface)
        t = Html(page)
        view = t.by_id("q-q1a")
        text = said(t.text(view))
        assert said(t.text(t.by_id("q-q1a:evidence"))) == "No rule tests this question yet.", surface
        assert text.count("No rule tests this question yet") == 1 and "No test linked yet" not in text and not t.of_class("empty", view), surface
        assert not any(e["attrs"].get("href") == "#notes" for e in t.all(view)) and "Notes on the project" not in text, surface
        cmds = [e for e in t.all(view) if e["tag"] == "pre" and e["attrs"].get("class") == "cmd"]
        assert [said(t.text(e)) for e in cmds] == [do], surface
        if surface == "export":
            # the owner's view and a file with no viewer copy it; a reader's draws none of it (shell.css by data-vw)
            box = next(e for e in t.all(view) if e["attrs"].get("data-vw") and t.inside(cmds[0], e))
            assert box["attrs"]["data-vw"] == "none owner", box["attrs"]
            reader = Html(page, "reader")
            rv = reader.by_id("q-q1a")
            assert not [e for e in reader.all(rv) if e["tag"] == "pre"] and said(reader.text(reader.by_id("q-q1a:evidence"))) == "No rule tests this question yet."
        heads = [said(t.text(e)) for e in t.all(view) if e["tag"] == "h2" and t.has(e, "qnh")]
        notes = [said(t.text(e)) for e in t.all(view) if e["tag"] == "li" and t.has(e, "pnote")]
        assert heads == ["Notes about this question"] and len(notes) == 2, surface
        assert notes[0].endswith(said(doc["home"]["notes"][0]["text"])) and notes[1] == "q1a has no rule yet: one seed at 512 images.", notes
        assert t.by_id("q-q1a:rail")["attrs"]["data-rootname"] == q["text"], surface
        assert "Experiments behind this question" in said(t.text(t.by_id("q-q1a:rail")))
        assert "Rules:" not in text, surface
    # nothing names it and rb gives no command: the sentence alone
    del q["cited_by"]
    doc["open_items"].pop()
    t = Html(page_of(record, doc))
    view = t.by_id("q-q1a")
    assert not t.of_class("qnh", view)
    # a top-level question with nothing linked says the same sentence, never rb's standing line in its place
    doc["questions"][0]["findings"] = []
    t = Html(page_of(record, doc))
    assert said(t.text(t.by_id("q-q1:evidence"))) == "No rule tests this question yet." and doc["home"]["standing"][0]["line"] not in said(t.text(t.by_id("q-q1"))) and not [e for e in t.all(view) if e["tag"] == "pre" and e["attrs"].get("class") == "cmd"]


def test_the_open_items_line_opens_its_list_beside_the_view_it_is_on(record, tmp_path):
    """P12 (plain words pass 4): the Overview's line under the queue, and Your call's, open the list of what else is
    open in the record in the inspector, headed by its name; the working view stays where it was, and Back closes it.
    Read in a real Chrome, through rb page's script."""
    at = tmp_path / "page.html"
    at.write_text(page_of(record, example()), encoding="utf-8")
    js = _cdp_script(tmp_path / "p12.mjs", """
const out = {};
await cdp.run(async () => {
  const page = await (await cdp.launchChrome()).page();
  await page.viewport(1440, 900);
  const probe = "({view: document.querySelector('main .view.on').id, insp: (document.querySelector('#insp .ibody .tt') || {}).textContent || '', crumbs: (document.querySelector('#insp .crumbs') || {}).textContent || '', hash: location.hash})";
  for (const [from, sel] of [['overview', '#queue .openrec a'], ['your-call', '#your-call .oilink a']]) {
    await page.goto(URL + '#' + from); await cdp.sleep(500);
    await page.ev("[...document.querySelectorAll('button')].filter(b => b.textContent.trim() === 'Skip').forEach(b => b.click())");
    await page.ev(`document.querySelector(${JSON.stringify(sel)}).click()`); await cdp.sleep(400);
    out[from] = await page.ev(probe);
    await page.ev("history.back()"); await cdp.sleep(400);
    out[from + ':back'] = await page.ev(probe);
  }
  await cdp.stopAll();
  return {};
}, { timeoutMs: 120000 });
console.log(JSON.stringify({ ok: true, out }));
""".replace("URL", json.dumps(at.as_uri())))
    code, got, err = _runner(_node(), js, env=dict(os.environ), timeout=180)
    assert code == 0 and got.get("ok"), err[-3000:]
    for view in ("overview", "your-call"):
        o, back = got["out"][view], got["out"][view + ":back"]
        assert o["view"] == view and said(o["insp"]) == "Also open in the record (3)" and o["hash"] == "#open-items", o
        assert back["view"] == view and "Also open in the record" not in back["insp"], back


def test_no_line_opens_an_open_items_list_that_has_no_row(record):
    """The line under the queue links to the list of what else is open only where that list has a row: an open item
    whose rows all fall away (a late item on no rule the page draws) leaves the line as words, never a dead link."""
    doc = example()
    doc["open_items"] = [{"code": "late", "head": "Declared after the evidence existed", "n": 1, "under": "", "items": []}]
    doc["unfiled"]["open_items"] = []
    t = Html(page_of(record, doc))
    assert t.by_id("open-items") is None and not t.of_class("oilink")
    line = t.of_class("openrec", t.by_id("queue"))[0]
    assert t.has(line, "dim") and not [e for e in t.all(line) if e["tag"] == "a"]


def test_a_questions_crumb_names_titles_and_its_state_word_once(record):
    """Plain words pass 4: the breadcrumb names each question above by its title cut at about 40 characters with an
    ellipsis, its id dim after it; an experiment's too. A state word that already names its subject ("Open question",
    as rb's model now gives it) is said once on the card and the question's page, never "Open question question"."""
    doc = example()
    long = "Does telling a small AI model to think step by step make it solve more grade-school math problems?"
    doc["questions"][0]["text"] = long
    for s in doc["home"]["standing"]:
        s["state_word"] = "Open question"
    t = Html(page_of(record, doc))
    crumb = t.of_class("crumb", t.by_id("q-q1a"))[0]
    assert said(t.text(crumb)) == f"Research › Does telling a small AI model to think… q1 › How many calibration images does INT8 need? q1a"
    assert [e["attrs"]["href"] for e in t.all(crumb) if e["tag"] == "a"] == ["#research", "#q-q1"]
    assert [said(t.text(e)) for e in t.all(crumb) if t.has(e, "cid")] == ["q1", "q1a"]
    x = doc["experiments"][0]
    xc = t.of_class("crumb", t.by_id(f"x-{x['id']}"))[0]
    assert said(t.text(xc)).startswith("Experiments › ") and said(t.text(xc)).endswith(f" {x['id']}") and said(x["title"][:20]) in said(t.text(xc))
    page = said(t.text(t.by_id("overview"))) + said(t.text(t.by_id("q-q1")))
    assert "Open question question" not in page and "Open question" in page


# ---------------------------------------------------------------- R4: Experiments, an experiment, Runs, Resources, History

def test_experiments_rows_carry_state_and_quiet_design_standing(record):
    """Experiments: a row per experiment in rb's order, its title, id, rb's state with its hint, its design standing
    quietly, and a link to its Reproduce with rb's ready line."""
    doc = example()
    t = Html(page_of(record, doc))
    view = t.by_id("experiments")
    rows = [e for e in t.all(view) if t.has(e, "srow")]
    assert [r["attrs"]["data-go"] for r in rows] == [f"x-{x['id']}" for x in doc["experiments"]]
    for r, x in zip(rows, doc["experiments"]):
        said_ = said(t.text(r))
        # design pass 1: the title first and the id trailing; Reproduce's readiness as one sentence, rb's ready line on
        # its hover (target 6)
        ok = sum(1 for row in x["reproduce"]["ready"] if row["state"] == "ok")
        assert said_.startswith(x["short_title"]) and x["design"]["standing"] in said_ and f"Rerun recipe: {ok} of {len(x['reproduce']['ready'])} parts ready" in said_
        assert x["reproduce"]["ready_line"] not in said_
        link = next(e for e in t.all(r) if e["attrs"].get("href") == f"#x-{x['id']}:reproduce")
        assert link["kids"][0]["attrs"]["title"] == x["reproduce"]["ready_line"]
        assert said(t.text(t.of_class("cid", r)[0])) == x["id"]
        assert any(e["attrs"].get("aria-describedby") == "g-locked" for e in t.all(r))


def test_design_standing_once(record):
    """An experiment's view says its design standing once: rb's word, unless rb's own line already says it, then what
    changed since it was confirmed and each part not recorded."""
    doc = example()
    t = Html(page_of(record, doc))
    for x in doc["experiments"]:
        view = said(t.text(t.by_id(f"x-{x['id']}")))
        assert view.count(x["design"]["standing"]) == 1, x["id"]
        for line in x["design"]["lines"]:
            assert said(line) in view
        for part in x["design"]["not_recorded"]:
            assert f"{part}: not recorded" in view
    doc["experiments"][1]["design"]["lines"] = []
    view = said(Html(page_of(record, doc)).text(Html(page_of(record, doc)).by_id("x-e2")))
    assert view.count("design not confirmed") == 1


def test_design_standing_in_rbs_words_from_rbs_code(record):
    """rb's data names a design's standing by a code (the real model's `not_confirmed`, `changed`): the row and the
    view say it in rb's words, never the code, and the view says it once beside rb's own line that leads with it."""
    doc = example()
    for x, code in zip(doc["experiments"], ("not_confirmed", "changed")):
        x["design"]["standing"] = code
    doc["experiments"][0]["design"]["lines"] = ["design not confirmed \u00b7 list it to confirm: rb confirm e1"]
    for surface in ("page", "export"):
        t = Html(page_of(record, doc, surface=surface))
        for x in doc["experiments"]:
            row = next(e for e in t.all(t.by_id("experiments")) if e["attrs"].get("data-go") == f"x-{x['id']}")
            view = said(t.text(t.by_id(f"x-{x['id']}")))
            for where in (said(t.text(row)), view):
                assert "not_confirmed" not in where and "changed" not in where.replace("changed since", ""), x["id"]
            assert "design not confirmed" in said(t.text(row)) and view.count("design not confirmed") == 1, (surface, x["id"])


def test_reproduce_says_how_rb_knows_each_command_from_its_own_words_first(record):
    """Reproduce's commands say how rb knows each: Reproduce's own words where it hands them (`given`), else the run's
    in the same document, else "observed" where rb says it watched, and nothing it would have to guess."""
    doc = example()
    cmds = doc["experiments"][0]["reproduce"]["commands"]
    cmds[0]["given"] = "logged"
    doc["runs"][cmds[1]["run"]].pop("command", None)
    t = Html(page_of(record, doc))
    # each run by its label, never its id (Look 7)
    label = {c["run"]: doc["runs"][c["run"]]["label"] for c in cmds}
    items = [said(t.text(e)) for e in t.all(t.by_id("x-e1:reproduce")) if e["tag"] == "li" and any(a["tag"] == "a" and said(t.text(a)) in label.values() for a in t.all(e))]
    one, two = label[cmds[0]["run"]], label[cmds[1]["run"]]
    assert items[0].startswith(f"{one} logged") and doc["runs"][cmds[0]["run"]]["command"]["kind"] not in items[0].split(cmds[0]["command"])[0]
    assert items[1].startswith(f"{two} {cmds[1]['command']}") or items[1].split(cmds[1]["command"])[0].strip() == two


def test_an_experiment_reads_its_facts_comparison_and_runs(record):
    """An experiment's view: its who line, its facts verbatim as author text, its comparison with rb's means, their n
    and each criterion's reading, then right under it the runs that make them, a retracted run folded apart with rb's
    line, then its criteria."""
    doc = example()
    t = Html(page_of(record, doc))
    x = doc["experiments"][0]
    view = t.by_id("x-e1")
    order = [e["attrs"]["id"] for e in t.all(view) if e["attrs"].get("id") in ("x-e1:cmp", "x-e1:runs", "x-e1:criteria", "x-e1:reproduce")]
    assert order == ["x-e1:cmp", "x-e1:runs", "x-e1:criteria", "x-e1:reproduce"]
    authored = [said(t.text(e)) for e in t.of_class("au", view)]
    assert x["hypothesis"] in authored and x["why_part"][1] in authored
    cmp = said(t.text(t.by_id("x-e1:cmp")))
    assert "0.7568 · 3 seeds" in cmp and "0.0029 lower than FP32" in cmp
    runs = t.by_id("x-e1:runs")
    standing = [r for r in x["run_ids"] if not doc["runs"][r]["retracted"]]
    rows = [e for e in t.all(runs) if e["tag"] == "tr" and e["attrs"].get("data-go")]
    assert [r["attrs"]["data-go"] for r in rows] == [f"run-{r}" for r in standing + ["runs2"]]
    assert t.has(rows[-1], "gone") and "id" not in rows[0]["attrs"]
    # its rules, a light row each opening the rule, its id trailing (design pass 1, target 3)
    rows_ = t.of_class("xrule", t.by_id("x-e1:criteria"))
    assert [r["attrs"]["data-go"] for r in rows_] == [f"c-{c}" for c in x["findings"]]
    assert [said(t.text(t.of_class("cid", r)[-1])) for r in rows_] == x["findings"]


def test_reproduce_from_the_document_only(record):
    """Reproduce draws what the document's recipe holds, as rb gave it: the ready line, the whole script verbatim, a
    readiness row each, each step with its command and its source, the per-run values, the settings, the commands as
    given apart (observed only where rb watched), and the Next step in words; never a command rb did not give."""
    doc = example()
    t = Html(page_of(record, doc))
    rep = doc["experiments"][0]["reproduce"]
    panel = t.by_id("x-e1:reproduce")
    text_ = said(t.raw(panel))
    assert "Rerun recipe: 2 of 4 parts ready" in text_ and f"\"{rep['prompt']}\"" in text_
    assert any(e["attrs"].get("title") == rep["ready_line"] for e in t.all(panel))
    script = t.by_id("x-e1:recipe")
    assert script["tag"] == "pre" and t.raw(script) == rep["script"]
    assert any(e["attrs"].get("data-copybtn") == "x-e1:recipe" for e in t.all(panel))
    rows = [e for e in t.all(panel) if e["tag"] == "li" and e["up"]["tag"] == "ul" and t.has(e["up"], "ready")]
    assert len(rows) == len(rep["ready"])
    for li, row in zip(rows, rep["ready"]):         # the kind in its column, rb's sentence after it, the outcome first
        kind, d = (said(t.raw(e)) for e in (t.of_class("w", li)[0], t.of_class("d", li)[0]))
        assert kind == row["label"] and d.startswith(row["state"])
        assert said(row["text"]).removeprefix(row["label"]).lstrip(" :\u00b7") in d
    cmds = [t.raw(e) for e in t.all(panel) if e["tag"] == "pre" and e is not script and not t.has(e, "prompt")]
    given = [s["command"] for s in rep["steps"] if s["command"]] + [m["command"] for m in rep["made"]] + [c["command"] for c in rep["commands"]]
    assert sorted(cmds) == sorted(given)
    assert not re.search(r"rb run add|rb experiment add|git checkout|https?:", text_)
    commands = said(t.raw(next(e for e in t.all(panel) if e["tag"] == "details" and e["attrs"].get("data-fold", "").endswith(":commands"))))
    label = {r: doc["runs"][r]["label"] for r in ("runs3", "runsql7")}
    assert f"{label['runs3']} observed" in commands and f"{label['runsql7']} given later, not observed" in commands and "runs3" not in commands
    doc["experiments"][0]["reproduce"]["commands"][0]["observed"] = False
    doc["runs"]["runs3"]["command"]["kind"] = "at the time"
    t = Html(page_of(record, doc))
    fold = next(e for e in t.all(t.by_id("x-e1:reproduce")) if e["tag"] == "details" and e["attrs"].get("data-fold", "").endswith(":commands"))
    assert "observed" not in said(t.raw(fold)).replace("not observed", "") and f"{label['runs3']} at the time" in said(t.raw(fold))
    doc["runs"]["runs3"]["command"]["kind"] = ""        # rb says nothing of how it knows: the page says nothing either
    t = Html(page_of(record, doc))
    fold = next(e for e in t.all(t.by_id("x-e1:reproduce")) if e["tag"] == "details" and e["attrs"].get("data-fold", "").endswith(":commands"))
    assert said(t.raw(fold)).startswith(f"Commands as recorded (history, not the recipe){label['runs3']} python train.py")


def test_reproduce_says_each_kind_once_and_each_outcome_in_its_place(record):
    """Reproduce as rb core words it: a readiness row puts its kind ("code") in the kind's column and its outcome
    ("ok") once, first in rb's sentence, never "ok | ok · commit ..." nor "code | code: unknown · ..."; a step whose
    label leads with its kind says it once ("checkpoint · int8-ckpt"); the recipe's own command step says how many
    runs record their command, and rb's line saying the same is not drawn under it again."""
    doc = example()
    rep = doc["experiments"][0]["reproduce"]
    rep["ready"] = [
        {"state": "ok", "label": "code", "text": "ok \u00b7 commit 2982f0fae9, b008869efc", "refs": []},
        {"state": "unknown", "label": "environment", "text": "environment: unknown \u00b7 the runs were made before they were recorded", "refs": []},
        {"state": "ok", "label": "checkpoint", "text": "ok \u00b7 int8-ckpt: present", "refs": []},
    ]
    rep["steps"] = [
        {"n": 1, "what": "code", "label": "code \u00b7 ok: commit 2982f0fae9", "refs": [], "command": "", "from": "", "runnable": False},
        {"n": 2, "what": "checkpoint", "label": "checkpoint \u00b7 int8-ckpt", "refs": [], "command": "python quantize.py --precision int8", "from": "", "runnable": True},
        {"n": 3, "what": "command (as given)", "label": "command (as given) \u00b7 recorded for all 4 runs", "refs": [], "command": "", "from": "", "runnable": False},
    ]
    rep["command_as_given"]["line"] = "every run records its command (as given)"
    t = Html(page_of(record, doc))
    panel = t.by_id("x-e1:reproduce")
    rows = [e for e in t.all(panel) if e["tag"] == "li" and e["up"]["tag"] == "ul" and t.has(e["up"], "ready")]
    got = [(said(t.raw(t.of_class("w", li)[0])), said(t.raw(t.of_class("d", li)[0]))) for li in rows]
    assert got == [
        ("code", "ok \u00b7 commit 2982f0fae9, b008869efc"),
        ("environment", "unknown \u00b7 the runs were made before they were recorded"),
        ("checkpoint", "ok \u00b7 int8-ckpt: present"),
    ]
    steps = next(e for e in t.all(panel) if e["tag"] == "ol" and t.has(e, "steps"))
    heads = [said(t.raw(e)) for e in t.of_class("sl", steps)]
    assert heads[:3] == ["code \u00b7 ok: commit 2982f0fae9", "checkpoint \u00b7 int8-ckpt", "command (as given) \u00b7 recorded for all 4 runs"]
    assert said(t.raw(steps)).count("command (as given)") == 1 and rep["command_as_given"]["line"] not in t.raw(steps)
    made = rep["made"]
    if made:                                         # what a run made sits under the command step that says it
        step = next(e for e in t.all(steps) if e["tag"] == "li" and said(t.raw(e)).startswith("command (as given)"))
        assert said(made[0]["line"]) in said(t.raw(step))


def test_runs_stand_and_retracted_stay_last(record):
    """Runs (design pass 1, target 5): how many runs there are and how many retracted stay on record ("8 runs · 1
    retracted stays on record"), then one table per experiment, its title first and its id trailing; a row per run
    that opens the run's card, which holds its receipt; a retracted run last in its table, struck and tagged."""
    doc = example()
    t = Html(page_of(record, doc))
    view = t.by_id("runs")
    assert "8 runs · 1 retracted stays on record" in said(t.text(view)) and "not retracted" not in said(t.text(view))
    rows = [r for r in t.all(view) if r["tag"] == "tr" and r["attrs"].get("data-go")]
    assert sorted(r["attrs"]["id"] for r in rows) == sorted(f"rr-{r}" for r in doc["runs"])
    e1 = [r for r in rows if r["attrs"]["id"][3:] in doc["experiments"][0]["run_ids"]]
    assert e1[-1]["attrs"]["id"] == "rr-runs2" and t.has(e1[-1], "gone") and said(t.text(t.of_class("tag", e1[-1])[0])) == "run retracted"
    heads = [e for e in t.all(view) if t.has(e, "grouphd")]
    assert said(t.text(heads[0])).startswith(doc["experiments"][0]["title"]) and "e1" in said(t.text(heads[0]))
    assert not said(t.text(heads[0])).startswith("e1")
    r1 = next(r for r in rows if r["attrs"]["id"] == "rr-runs1")
    card = t.by_id("run-runs1")
    assert card is not None and r1["attrs"]["data-go"] == "run-runs1"
    for field, said_, _ in doc["runs"]["runs1"]["receipt_rows"]:
        assert said(said_) in said(t.text(card)) and said(said_) not in said(t.text(r1))
    assert [e["attrs"]["href"] for e in t.all(r1) if e["tag"] == "a"] == ["#run-runs1"]
    assert t.by_id("runs-analyses") is not None and "per-class accuracy" in said(t.text(t.by_id("runs-analyses")))


def test_resources_say_how_to_get_each_again(record):
    """Resources: a row per resource, its name, type and role, the runs that read it and the criteria resting on it,
    its availability, and how to get it again on the row."""
    doc = example()
    t = Html(page_of(record, doc))
    view = t.by_id("resources")
    for rid, r in doc["resources"].items():
        row = next(e for e in t.of_class("resrow", view) if e["attrs"]["data-for"] == f"r-{rid}")
        summary = said(t.text(row["kids"][0]))
        assert r["name"] in summary and r["availability"] in summary
        if r["acquisition"]:
            assert f"how to get it again · {r['acquisition']['command']}" in summary
        if r["read_by"]["runs"]:
            assert f"read by {len(r['read_by']['runs'])} run" in summary


def test_history_as_log_rows_newest_first(record):
    """History, before its sittings: rb's line, how many writes the log holds, then every write newest first with its
    time and rb's sentence, each at its own anchor, naming no writer."""
    doc = example()
    t = Html(page_of(record, doc))
    rows = [e for e in t.all(t.by_id("history")) if e["tag"] == "li" and e["attrs"].get("id", "").startswith("log-")]
    want = sorted(doc["history"]["log"], key=lambda e: -e["log_index"])
    assert [r["attrs"]["id"] for r in rows] == [f"log-{e['log_index']}" for e in want]
    for r, e in zip(rows, want):
        assert said(t.raw(r)).startswith(said(f"{e['at']} {e['sentence']}"))
        objects = {q["id"] for q in doc["questions"]} | {x["id"] for x in doc["experiments"]} | set(doc["findings"]) | set(doc["runs"]) | set(doc["resources"])
        objects |= {a for a in doc["analyses"] if t.by_id(f"a-{a}") is not None}    # an analysis is linked where its card is drawn
        # a run by its label, an analysis by its title, never by its id (Look 7); a chosen id as it is
        named = lambda x: doc["runs"][x]["label"] if x in doc["runs"] else doc["analyses"][x]["title"] if x in doc["analyses"] else x
        refs = list(dict.fromkeys(x for x in e["refs"] if x in objects))
        links = [a for a in t.all(r) if a["tag"] == "a"]
        assert len(links) == len(refs)
        # each object once (FIX9): linked inside the sentence where it says it, else after it, never both
        for x in refs:
            forms = [said(f) for f in (doc["runs"].get(x, {}).get("named", ""), named(x)) if f]
            text_ = said(t.text(r))
            assert any(len(re.findall(rf"(?<![\w-]){re.escape(f)}(?![\w-])", text_)) == 1 for f in forms), (x, text_)
            if forms[0] in said(e["sentence"]):        # said apart in the sentence: nothing after it says it again
                assert not any(t.has(s, "refs") and forms[0] in said(t.text(s)) for s in t.all(r)), (x, text_)
    assert not words_problems(page_of(record, doc))


def test_history_draws_its_sittings_and_the_overview_its_newest(record):
    """History draws the model's sittings (H's history_view) above the log rows, each sitting by its head, newest
    first; the Overview says rb's Latest changes line linked to History, and the newest sitting under Recent calls and
    changes, both in the Recent changes fold, after the queue and before the notes."""
    doc = example()
    t = Html(page_of(record, doc))
    hist = t.by_id("history")
    sits = t.of_class("sit", hist)
    assert [said(t.text(t.of_class("sith", s)[0])) for s in sits] == [s["head"] for s in doc["history"]["sittings"]]
    assert said(t.text(t.of_class("hsum", hist)[0])) == doc["history"]["summary"]
    assert len([e for e in t.all(hist) if e["attrs"].get("class") == "counts"]) == 0, "the summary is said once"
    log = next(e for e in t.all(hist) if e["tag"] == "ol" and "log" in e["attrs"].get("class", ""))
    assert t.raw(hist).index(t.raw(sits[-1])) < t.raw(hist).index(t.raw(log)), "the sittings come before the log rows"
    ov = t.by_id("overview")
    latest = t.of_class("latest", ov)[0]
    link = next(e for e in t.all(latest) if e["tag"] == "a")
    assert link["attrs"]["href"] == "#history" and said(t.text(link)) == doc["home"]["latest"]["line"]
    assert len(t.of_class("latest", ov)) == 1, "one place draws the Latest changes line"
    recent = t.of_class("recent", ov)[0]
    # the newest sitting's head is said once on the Overview: the Latest changes line holds it whole (H may drop the
    # sitting's own head from recent(), P10: one line said once)
    assert doc["history"]["sittings"][0]["head"] in said(t.text(latest))
    assert all(said(t.text(h)) == doc["history"]["sittings"][0]["head"] for h in t.of_class("sith", recent))
    order = list(t.all(ov))
    head = next(e for e in order if e["tag"] == "h2" and said(t.text(e)) == "Recent calls and changes")
    notes = next(e for e in order if e["attrs"].get("data-fold") == "notes")
    fold = next(e for e in order if e["attrs"].get("data-fold") == "recent")
    assert t.inside(latest, fold) and t.inside(recent, fold) and t.inside(head, fold)
    assert order.index(t.by_id("queue")) < order.index(fold) and order.index(head) < order.index(recent) < order.index(notes)
    dead = {e["attrs"]["href"][1:] for e in [*t.all(ov), *t.all(hist)] if e["tag"] == "a" and e["attrs"].get("href", "").startswith("#")} - {e["attrs"].get("id") for e in t.all()}
    assert not dead, sorted(dead)
    assert not words_problems(page_of(record, doc))


def test_a_unit_amended_after_the_lock_links_to_its_amendment(record):
    """A finding whose experiment amended its unit after the lock says so in its row's disclosure, linked to that
    amendment, which the experiment's view holds at its own anchor with its time."""
    doc = example()
    t = Html(page_of(record, doc))
    am = doc["findings"]["c5"]["unit_amended"]
    link = next(e for e in t.all(t.by_id("f-c5:q1a")) if e["tag"] == "a" and said(t.text(e)) == am["line"])
    assert link["attrs"]["href"] == f"#x-{am['amendment']}"
    target = t.by_id(f"x-{am['amendment']}")
    assert target is not None and t.inside(target, t.by_id("x-e2"))


def test_an_interpretation_never_reads_as_confirmed_unless_rb_says_so(record):
    """An interpretation whose `confirmed` is absent reads as not confirmed, on its card and in its group; on a question,
    one the user confirmed leads above a session's note, whatever each rests on; and the note form is set apart from the
    answer's type, as on a card."""
    doc = example()
    q = doc["questions"][0]
    first = q["overview"]["interpretations"][0]
    q["overview"]["interpretations"] = [dict(first, text="Calibration on 512 images is enough.", confirmed=False, criteria=["c1", "c3", "c4"]),
                                        dict(first)]
    del q["overview"]["interpretations"][1]["confirmed"]
    q["overview"]["interpretations"].append(dict(first, text="INT8 holds within half a point.", criteria=["c2"]))
    del q["lead"]["confirmed"]
    del doc["home"]["standing"][0]["interpretation"]["confirmed"]
    t = Html(page_of(record, doc))
    groups = [e for e in t.all(t.by_id("q-q1")) if t.has(e, "igroup") and "id" in e["attrs"]]
    assert [g["attrs"]["id"] for g in groups] == ["q-q1:ip:3", "q-q1:ip:1", "q-q1:ip:2"]
    for g in groups[1:]:
        assert t.has(next(e for e in t.all(g) if e["tag"] == "h3"), "note"), g["attrs"]["id"]
    card = t.by_id("st-q1")
    assert not t.of_class("tag", card) and said(t.text(t.of_class("sti", card)[0])).startswith("A note, not confirmed: ")
    rules = css(page_of(record))[""]
    assert rules[".igroup > .said.note"]["font-weight"] == "400"


def test_a_call_made_draws_only_its_note_as_the_writers(record):
    """A call made on a question's work is rb's line, and only the note rb gives after its "note: " is drawn as the
    writer's words, even when the note's words also occur in rb's part of the line."""
    doc = example()
    m = next(m for m in doc["made_calls"] if m["note"])
    m["note"], m["line"] = "accepted", "accepted 2026-10-15 · note: accepted · not confirmed"
    t = Html(page_of(record, doc))
    li = next(e for e in t.all(t.by_id("q-q1:rail")) if e["tag"] == "li" and "note: accepted" in said(t.text(e)))
    spans = [k for k in li["kids"] if isinstance(k, dict)]
    assert [said(t.raw(k)) for k in spans if t.has(k, "au")] == ["accepted"]
    # the call's line first, what it is on trailing as its id (design pass 1, P3), opening it
    assert said(t.raw(li)) == f"accepted 2026-10-15 · note: accepted · not confirmed {m['subject']}"
    assert [e["attrs"]["href"] for e in t.all(li) if e["tag"] == "a"] and t.of_class("cid", li)
    assert t.raw(spans[[t.has(k, "au") for k in spans].index(True) - 1]).endswith("note: ")


def test_a_cited_criterion_says_so_in_its_details(record):
    """A criterion whose rule cites a number someone else stated says "cited" in its row's details."""
    t = Html(page_of(record))
    dd = next(e for e in t.all(t.by_id("f-c2:q1")) if e["tag"] == "dd")
    assert said(t.text(dd)).endswith("· cited")
    dd = next(e for e in t.all(t.by_id("f-c1:q1")) if e["tag"] == "dd")
    assert "cited" not in said(t.text(dd))


def test_an_experiment_draws_rbs_digits_and_no_mark_on_a_design_the_user_owns(record):
    """A comparison's means are in rb compare's digits, never a float's whole form; a design the user wrote or confirmed
    as it stands carries no standing word, on its view or its row; with no Reproduce, nothing links to one; past 14
    standing runs the rest fold; the lock's anchor is the who line, said once; a resource's way back that is a sentence
    is drawn as text, not code."""
    doc = example()
    x = doc["experiments"][0]
    x["table"]["rows"][0]["metrics"]["top1"]["mean"] = 0.7200000000000001
    x["table"]["rows"][0]["metrics"]["latency_ms"]["mean"] = 5.0
    x["design"] = {"standing": "yours", "pending": False, "confirm": "", "lines": [], "not_recorded": []}
    doc["experiments"][1].pop("reproduce")
    base = doc["runs"]["runs1"]
    for i in range(20):
        doc["runs"][f"runx{i}"] = dict(base, run_id=f"runx{i}", label=f"fp32 and int8 · seed {10 + i}")
        x["run_ids"].append(f"runx{i}")
    doc["resources"]["resnet50-fp32"]["how"] = "downloaded from the model zoo's v1.5 page"
    t = Html(page_of(record, doc))
    cmp = said(t.text(t.by_id("x-e1:cmp")))
    # a mean in a screen's digits (P5, PIVOT: two decimals, more where its column's values need them), never a float's
    # whole form; one unit is one run
    cells = [said(t.text(e)) for e in t.all(t.by_id("x-e1:cmp")) if e["tag"] == "td"]
    assert "0.720 · 3 seeds" in cmp and "0.7200000000000001" not in cmp and "5.00 · 1 run" in cells and "5.0 " not in cmp
    view = said(t.text(t.by_id("x-e1")))
    assert "yours" not in view and "not confirmed" not in view
    row = next(e for e in t.all(t.by_id("experiments")) if t.has(e, "srow") and e["attrs"]["data-go"] == "x-e1")
    assert "yours" not in said(t.text(row))
    row2 = next(e for e in t.all(t.by_id("experiments")) if t.has(e, "srow") and e["attrs"]["data-go"] == "x-e2")
    assert "Reproduce" not in said(t.text(row2)) and t.by_id("x-e2:reproduce") is None
    # one table of every run (design pass 1, target 5): a long one is a table still, never cut into folds
    runs = t.by_id("x-e1:runs")
    table = next(e for e in t.all(runs) if e["tag"] == "table")
    body = next(e for e in t.all(table) if e["tag"] == "tbody")
    assert len([e for e in t.all(body) if e["tag"] == "tr"]) == len(x["run_ids"]) and not [e for e in t.all(runs) if e["tag"] == "details"]
    assert t.by_id("x-e1:lock")["tag"] == "p" and view.count(x["who_line"]) == 1 and x["who_line"].startswith("locked ")
    res = next(e for e in t.of_class("resrow", t.by_id("resources")) if e["attrs"]["data-for"] == "r-resnet50-fp32")
    assert "how to get it again · downloaded from the model zoo's v1.5 page" in said(t.text(res)) and not [e for e in t.all(res) if e["tag"] == "code"]


# ---------------------------------------------------------------- what rb open's live module and the hosted page read

def test_every_fold_has_a_stable_key(record):
    """Every <details> the shell draws carries data-fold, a key unique in the page, named for the part it folds, and the
    same when the record around it changes, so rb open's live module keeps a fold open across a swap."""
    doc = example()
    t = Html(page_of(record, doc))
    folds = [e for e in t.all() if e["tag"] == "details"]
    keys = [e["attrs"].get("data-fold") for e in folds]
    assert all(keys) and len(keys) == len(set(keys)), [k for k in keys if not k or keys.count(k) > 1]
    for want in ("f-c1:q1:details", "q-q1:ip:1:when", "cr-c1", "rs-imagenet-val", "call-c2:row", "x-e1:script", "notes",
                 "oi:late"):
        assert want in keys, want
    more = copy.deepcopy(doc)
    more["runs"]["runs9"] = dict(doc["runs"]["runs1"], run_id="runs9", label="fp32 and int8 · seed 9")
    more["experiments"][0]["run_ids"].append("runs9")
    again = [e["attrs"].get("data-fold") for e in Html(page_of(record, more)).all() if e["tag"] == "details"]
    # the new run's row, and its card's folds where the page draws cards
    # a run is a table row (design pass 1, target 5): the new run adds only its card's folds
    assert set(keys) <= set(again) and set(again) - set(keys)
    assert all(k.startswith("run-runs9:") for k in set(again) - set(keys)), set(again) - set(keys)


def test_the_scope_line_and_the_top_bar_say_what_changes_in_place(record):
    """rb's scope line stands in an element of its own whose whole text is the line (data-live="scope"); the top bar,
    outside #nav and #main and changing with the record, carries its id and data-swap."""
    doc = example()
    t = Html(page_of(record, doc))
    live = [e for e in t.all() if e["attrs"].get("data-live") == "scope"]
    assert len(live) == 1 and said(t.raw(live[0])) == said(doc["home"]["scope"])
    top = t.by_id("top")
    assert top["tag"] == "header" and "data-swap" in top["attrs"]


def test_rb_export_builds_its_own_document(record):
    """rb export asks the model for the export's document, which says so (surface "export"), and rb page for its own."""
    for surface, argv in (("page", ["page", "--out", str(record / "built.html")]), ("export", ["export", "--out", str(record / "built-export")])):
        dump = record / f"built-{surface}.json"
        r = rb(record, *argv, RB_TEST_SHELL="1", RB_TEST_SHELL_DUMP=str(dump))
        assert r.returncode == 0, r.stderr
        doc = json.loads(dump.read_text(encoding="utf-8"))
        assert doc["surface"] == surface and doc["tier"] == "owner"

def test_the_export_says_open_calls(record):
    """Polish 1 (Oct 22 00:10): on the export, which cannot know who reads it, Your call is "Open calls (N)" in the nav
    and its view, and the Overview's queue "Needs judgment" with its row count (D3, the Oct 25 00:00 +08 call), with
    rb's lead line, no action button and "See the open calls →"; #open-calls names the view as #your-call does;
    and no line of the views says "Your call", "waits on you" or "your terminal". rb page, read on the owner's own
    disk, keeps Your call."""
    page = page_of(record, surface="export")
    t = Html(page)
    nav = next(e for e in t.all(t.by_id("nav")) if e["tag"] == "a" and e["attrs"]["href"] == "#your-call")
    n, rows = example()["your_call"]["n"], len(t.of_class("qrow", t.by_id("queue")))
    assert said(t.text(nav)) == f"Open calls {n}" and said(t.text(t.by_id("h-your-call"))) == f"Open calls ({n})"
    assert said(t.text(t.by_id("h-queue"))) == "Needs judgment" and rows and not t.of_class("qbtn")
    # plain words pass 4: the queue's lead says what a row is for; Your call keeps how calls are made
    assert said(t.text(t.of_class("lede", t.by_id("queue"))[0])) == "Open one to see why it waits and decide it."
    assert said(t.text(t.of_class("lede", t.by_id("your-call"))[0])) == "Calls are made with rb in a terminal, never on this page."
    assert said(t.text(next(e for e in t.all(t.by_id("to-confirm")["up"]) if e["tag"] == "summary"))) == "Waiting for a confirmation"
    assert [said(t.text(e)) for e in t.all(t.by_id("queue")) if e["tag"] == "a" and e["attrs"]["href"] == "#your-call" and not t.has(e, "qa")] == ["See the open calls →"]
    assert t.inside(t.by_id("open-calls"), t.by_id("your-call"))
    views = " ".join(said(t.text(v)) for v in t.all() if t.has(v, "view"))
    # rb's line on why each question stands is drawn as rb gives it; its voice on a page that leaves the machine is
    # the model's (requested of M; test_a_real_exports_standing_line_is_no_ones holds it), so it alone is set aside here
    given = [said(s["line"]) for s in example()["home"]["standing"]]
    assert [said(t.text(e)) for e in t.of_class("str", t.by_id("overview"))] == given
    for line in given:
        views = views.replace(line, "")
    for word in ("Your call", "waits on you", "your terminal", "your own terminal", "your confirmation"):
        assert word not in views, word
    mine = Html(page_of(record))
    assert said(mine.text(mine.by_id("h-queue"))).startswith("Needs your judgment") and said(mine.text(mine.by_id("h-your-call"))).startswith("Your call (")


def test_a_real_exports_standing_line_is_no_ones(int8_demo, tmp_path):
    """Polish 1's export voice on contract 1.6's standing line: rb export's document says why each question stands in no
    one's voice, as its quiet line does (M's quiet_away), since anyone may be sent it. Skips while M's model words it
    for the owner (R's request to M, MP after MP-1022-09)."""
    dump = tmp_path / "export.json"
    r = subprocess.run([*command(), "export", "--out", str(tmp_path / "export")], cwd=int8_demo.root, capture_output=True, encoding="utf-8",
                       errors="replace", stdin=subprocess.DEVNULL, env={**int8_demo.env, "RB_TEST_SHELL": "1", "RB_TEST_SHELL_DUMP": str(dump)})
    assert r.returncode == 0, r.stderr
    lines = [s["line"] for s in json.loads(dump.read_text(encoding="utf-8"))["home"]["standing"]]
    if any("your " in line.lower() for line in lines):
        pytest.skip("pending M: home.standing[].line on rb export in no one's voice (R's request after MP-1022-09)")
    page = (tmp_path / "export" / "page.html").read_text(encoding="utf-8")
    assert all(html.escape(line) in page for line in lines)


def test_the_overview_draws_rbs_projection_when_it_gives_one(record):
    """Contract 1.6 (M's home.standing, home.integrity, home.queue, questions[].tallies): the Overview draws rb's line on
    why, its tallies, its interpretation, its integrity parts and the line on what they affect, and its queue in rank
    order, each row's button on the object it names as the page holds it, its heading counting its rows."""
    doc = example()
    doc["home"]["standing"] = [{"question": "q1", "text": doc["questions"][0]["text"], "state": "open", "state_word": "Open",
                                "line": "The record does not answer it yet: the fp32 baseline is not reproduced.", "reasons": [],
                                "tallies": [{"state": "meets", "n": 3, "label": "3 met", "criteria": ["c1"]},
                                            {"state": "not reproduced", "n": 1, "label": "1 not reproduced", "criteria": ["c2"]}],
                                "interpretation": {"line": "", "text": "", "holds": "", "confirmed": False}}]
    doc["home"]["integrity"] = {"parts": [{"kind": "after_evidence", "n": 2, "of": 6, "line": "2 of 6 criteria were declared after their evidence existed.", "ids": []},
                                          {"kind": "without_command", "n": 6, "of": 8, "line": "6 of the 8 runs record no command.", "ids": []}],
                                "affects": "Read them as a description of what happened, not as a test."}
    doc["home"]["queue"] = [
        {"kind": "design", "title": "Confirm 2 experiment designs", "detail": "int8 against fp32 · calibration set size", "target": "e1", "targets": ["e1", "e2"], "action": "Review", "rank": 3},
        {"kind": "baseline", "title": "Review the fp32 baseline mismatch", "detail": "observed 75.97 % · cited 76.13 %", "target": "c2", "targets": ["c2"], "action": "Review", "rank": 1},
        {"kind": "uncited", "title": "Inspect a changed file nothing in the record cites", "detail": "data.yaml", "target": "", "targets": [], "action": "Inspect", "rank": 5}]
    t = Html(page_of(record, doc))
    card = t.by_id("st-q1")
    assert said(t.text(t.of_class("str", card)[0])) == "The record does not answer it yet: the fp32 baseline is not reproduced."
    assert [said(t.text(e)) for e in t.all(t.of_class("stt", card)[0]) if t.has(e, "sw")] == ["3 met", "1 not reproduced"]
    # an empty interpretation in rb's projection means none is active: the question's lead is not drawn in its place
    assert not t.of_class("sti", card) and said(doc["questions"][0]["lead"]["text"]) not in said(t.text(card))
    box = t.by_id("integrity")
    assert [(said(t.text(e)), [a["attrs"]["href"] for a in t.all(e) if a["tag"] == "a"]) for e in box["kids"] if isinstance(e, dict) and e["tag"] == "p"] == [
        ("2 of 6 criteria were declared after their evidence existed.", ["#criteria"]), ("6 of the 8 runs record no command.", ["#runs"]),
        ("Read them as a description of what happened, not as a test.", [])]
    rows = t.of_class("qrow", t.by_id("queue"))
    assert [r["attrs"]["data-kind"] for r in rows] == ["baseline", "design", "uncited"]
    # two designs wait: their row opens the list that names each (design pass 1, P11)
    assert [next(e for e in t.all(r) if t.has(e, "qbtn"))["attrs"]["href"] for r in rows] == ["#call-c2", "#to-confirm", "#how-made"]
    assert said(t.text(rows[0])) == "Review the fp32 baseline mismatch observed 75.97 % · cited 76.13 % Review"
    assert said(t.text(t.by_id("h-queue"))) == "Needs your judgment"
    # Your call holds the calls at the Decide level, the queue's first, and none of what waits for a confirmation
    yc = t.by_id("your-call")
    assert [r["attrs"]["data-for"] for r in t.of_class("crow", yc)][0] == "call-c2" and not t.inside(t.by_id("to-confirm"), yc)
    doc["home"]["integrity"] = {"parts": [], "affects": ""}
    assert Html(page_of(record, doc)).by_id("integrity") is None
    # rb's interpretation, where it gives one, is the one drawn
    doc["home"]["standing"][0]["interpretation"] = {"line": "", "text": "INT8 holds within a third of a point.", "holds": "its 2 criteria met", "confirmed": True}
    t = Html(page_of(record, doc))
    # a recorded interpretation is a note (plain words pass 3), its rules' state a sentence of its own
    assert said(t.raw(t.of_class("sti", t.by_id("st-q1"))[0])) == "Note: INT8 holds within a third of a point. Its 2 rules are met."
    # an empty queue is rb's answer: nothing waits, and nothing from the older fields is added to it
    doc["home"]["queue"] = []
    t = Html(page_of(record, doc))
    assert not t.of_class("qrow") and said(t.text(t.by_id("h-queue"))) == "Needs your judgment"


def test_a_tag_every_row_shares_is_said_once_in_its_heading(record):
    """Design pass 1, P6: a tag marks an exception. Where most of a question's rules were written after their results,
    that is said once at the head of its rules, as a count ("All 4 rules were written after their results came in"),
    and no row carries it; a rule written before its results is then the exception and carries "written before its
    results". Where most were written before, only the late ones carry "rule written after the results". The note
    groups carry no tag: a note says whether it is confirmed in its own line."""
    doc = example()
    mine = doc["questions"][0]["findings"]
    for c in mine:
        doc["findings"][c]["tags"] = ["retrospective"]
    doc["findings"][mine[0]]["tags"] = ["retrospective", "uncertainty_remains"]
    for ip in doc["questions"][0]["overview"]["interpretations"]:
        ip["confirmed"] = False
    t = Html(page_of(record, doc))
    view = t.by_id("q-q1")
    late = t.of_class("late", view)
    assert [said(t.text(e)) for e in late] == [f"All {len(mine)} rules were written after their results came in"]
    assert late[0]["kids"][0]["attrs"]["aria-describedby"] == "g-rule-written-after-the-results"
    rows = [t.by_id(f"f-{c}:q1") for c in mine]
    assert all(not t.of_class("tag", r) for r in rows)
    groups = [e for e in t.all(view) if t.has(e, "igroup") and not t.has(e, "rest")]
    assert groups and all(not t.of_class("tag", g) for g in groups)
    doc["findings"][mine[1]]["tags"] = []
    t = Html(page_of(record, doc))
    assert [[said(t.text(e)) for e in t.of_class("tag", t.by_id(f"f-{c}:q1"))] for c in mine] == [[], ["written before its results"]] + [[]] * (len(mine) - 2)
    for c in mine[1:]:
        doc["findings"][c]["tags"] = []
    t = Html(page_of(record, doc))
    assert said(t.text(t.of_class("late", t.by_id("q-q1"))[0])) == f"1 of {len(mine)} rules was written after its results came in"
    assert [[said(t.text(e)) for e in t.of_class("tag", t.by_id(f"f-{c}:q1"))] for c in mine] == [["rule written after the results"]] + [[]] * (len(mine) - 1)


def test_a_recorded_interpretation_is_drawn_as_the_models_reading(record):
    """Plain words pass 3 (replacing film pass 2's labels): the standing card and the question view draw an active
    interpretation as a note, its state in its line ("Note: ..." where the user owns it, "A note, not confirmed: ..."
    where it waits, on every surface the same), the glossary's line for a note on its label, and never as a reading,
    which is rb's computed number."""
    doc = example()
    given = doc["home"]["standing"][0]["interpretation"]
    t = Html(page_of(record, doc))
    line = t.of_class("sti", t.by_id("st-q1"))[0]
    assert said(t.raw(line)) == f"Note: {given['text']} Its 2 rules are met."
    hint = next(e for e in t.all(line) if t.has(e, "term"))
    assert hint["attrs"]["aria-describedby"] == "g-note-on-a-question" and said(t.text(hint)) == "Note:"
    ips = doc["questions"][0]["overview"]["interpretations"]
    assert ips and all(ip.get("active", True) for ip in ips)
    for surface in ("page", "export"):
        for confirmed, label in ((True, "Note:"), (False, "A note, not confirmed:")):
            for ip in ips:
                ip["confirmed"] = confirmed
            t = Html(page_of(record, doc, surface))
            heads = [said(t.text(e)) for e in t.all(t.by_id("q-q1")) if e["tag"] == "h3" and t.has(e, "said")]
            assert heads and all(h.startswith(label) for h in heads), (surface, heads)
            assert not [e for e in t.all(t.by_id("q-q1")) if t.has(e, "tag") and "reading" in said(t.text(e)).lower()]

def test_an_experiments_first_screen(record):
    """OVERVIEW-V2: an experiment's view opens on what it compares (rb's line, the baseline labelled as one, contract
    1.6's compares) with what it varies, said there and not again in its facts; its design standing in one line; its
    hypothesis on one line, its whole text one click away (a fold, the text once); then the comparison. Settings sit
    under "Source details" and Reproduce's recipe under its own fold, never in the inspector, whose root is rb's line."""
    doc = example()
    x = doc["experiments"][0]
    x["compares"] = "int8 against the fp32 baseline"
    t = Html(page_of(record, doc))
    view = t.by_id(f"x-{x['id']}")
    assert view["attrs"]["data-root"] == "insp-none"
    kids = [k for k in view["kids"] if isinstance(k, dict)]
    cmpl = next(k for k in kids if t.has(k, "cmpl"))
    assert said(t.text(cmpl)) == f"int8 against the fp32 baseline · varies {', '.join(x['varies'])}"
    assert kids.index(cmpl) < kids.index(next(k for k in kids if t.has(k, "facts"))) < kids.index(next(k for k in kids if any(t.has(e, "grid") for e in t.all(k))))
    facts = next(k for k in kids if t.has(k, "facts"))
    assert "varies" not in [said(t.text(e)) for e in t.all(facts) if e["tag"] == "dt"]
    hyp = next(e for e in t.all(facts) if e["tag"] == "details" and t.has(e, "oneline"))
    assert said(t.text(hyp)) == said(x["hypothesis"]) and said(t.raw(facts)).count(said(x["hypothesis"])) == 1
    repro = t.by_id(f"x-{x['id']}:reproduce")
    fold = next(k for k in repro["kids"] if isinstance(k, dict))
    assert fold["tag"] == "details" and "open" not in fold["attrs"] and "data-rail" not in repro["attrs"]
    assert said(t.text(next(k for k in fold["kids"] if isinstance(k, dict)))).startswith("Reproduce")
    settings = next(e for e in t.all(view) if e["tag"] == "details" and e["attrs"].get("data-fold") == f"x-{x['id']}:settings-fold")
    assert said(t.text(next(k for k in settings["kids"] if isinstance(k, dict)))) == f"Source details · {x['settings_line']}"
    del x["compares"]
    t = Html(page_of(record, doc))
    facts = t.of_class("facts", t.by_id(f"x-{x['id']}"))[0]
    assert "varies" in [said(t.text(e)) for e in t.all(facts) if e["tag"] == "dt"] and not t.of_class("cmpl", t.by_id(f"x-{x['id']}"))


def test_all_criteria_labels_the_baseline(record):
    """OVERVIEW-V2: a baseline or reference is labelled as one wherever it appears: All criteria's row says rb's short
    reading (contract 1.6's reading_short, "fp32 baseline: observed ... · cited ...") with rb's result word and its tags,
    where rb gives it, and rb's estimate and result where it gives none."""
    doc = example()
    doc["findings"]["c2"]["reading_short"] = "fp32 baseline: observed 75.97 % · cited 76.13 %"
    doc["findings"]["c2"]["tags"] = ["retrospective"]
    t = Html(page_of(record, doc))
    row = next(e for e in t.all(t.by_id("criteria")) if e["attrs"].get("data-for") == "c-c2")
    # design pass 1, target 3: rb's reading as a plain sentence, the baseline labelled; rb's short reading with its
    # result word in the row's Details
    assert said(t.text(t.of_class("pr", row)[0])) == "FP32 baseline is 0.7597 (0.7591 to 0.7603) on Top-1, 3 seeds; the cited number is 0.7613."
    assert said(t.text(t.of_class("rbr", row)[0])) == "fp32 baseline: observed 75.97 % · cited 76.13 % · measured"
    other = next(e for e in t.all(t.by_id("criteria")) if e["attrs"].get("data-for") == "c-c1")
    assert "INT8 is 0.0029 lower than the FP32 baseline" in said(t.text(t.of_class("cv", other)[0]))


def test_a_questions_view_counts_as_its_card_does(record):
    """Codex pass 2: where rb gives a question's tallies (contract 1.6), its view counts what is linked here from them,
    as the Overview's card and row do, keeping rb's line on what sits below it; with none given, rb's rollup as it is."""
    doc = example()
    # rb gives the card the question's own tallies (M's overview computes them once for both)
    doc["questions"][0]["tallies"] = doc["home"]["standing"][0]["tallies"] = [{"state": "undetermined", "n": 17, "label": "17 undetermined", "criteria": []}]
    t = Html(page_of(record, doc))
    rolls = [said(t.text(e)) for e in t.of_class("roll", t.by_id("q-q1"))]
    below = doc["questions"][0]["rollup"].split("below it: ")[1] if "below it: " in doc["questions"][0]["rollup"] else None
    # the view's line is the card's, led by what it counts (plain words pass 3); what sits below it is its sub-questions'
    # own cards, never a chain here (design pass 1, target 1)
    assert rolls[-1] == "Rules: 17 can't tell yet" == said(t.text(t.of_class("stt", t.by_id("st-q1"))[0]))
    assert below is None or below not in rolls[-1]


def test_a_reproduce_link_stands_apart_from_its_ready_line(record):
    """Design pass 1, target 6 (replacing P's line 9): where an experiment's readiness to run again is said (Experiments,
    a question's rail), it is one plain sentence linked to its Reproduce ("Rerun recipe: 2 of 4 parts ready"), rb's
    ready line with its glyphs on its hover; the glyphs' font still holds them in Reproduce itself."""
    doc = example()
    page = page_of(record, doc)
    t = Html(page)
    ready = {f"#x-{x['id']}:reproduce": x["reproduce"] for x in doc["experiments"] if x.get("reproduce")}
    links = [e for e in t.all() if e["tag"] == "a" and e["attrs"].get("href") in ready and said(t.text(e)).startswith("Rerun recipe: ")]
    assert len(links) >= 2
    for link in links:
        r = ready[link["attrs"]["href"]]
        ok = sum(1 for row in r["ready"] if row["state"] == "ok")
        n = len(r["ready"])
        assert said(t.text(link)) == (f"Rerun recipe: all {n} parts ready" if ok == n else f"Rerun recipe: {ok} of {n} parts ready")
        assert link["kids"][0]["attrs"]["title"] == r["ready_line"]
    rules = css(page)[""]
    assert "Noto Sans Symbols 2" in rules[".g"]["font-family"] and "Noto Sans Symbols 2" in rules[".readyline"]["font-family"]

def test_command_boxes_keep_room_and_cue_scrolling(record):
    """C's line 29 and D's lines 18 and 19: every command box shows a shadow at an edge it scrolls past (the same cue
    as a table's), the command as given scrolls whole rather than breaking inside a flag, and a box that holds a Copy
    button (the composer's, a recipe's) keeps room for it, so the button never sits over the command. A's line 22: the
    theme icon follows the state the script sets on its button. Reproduce's Copy recipe sits in the recipe's own box,
    so its fold holds nothing that prints empty."""
    page = page_of(record)
    rules = css(page)
    assert "local" in rules[""][".cmd"]["background"] and "radial-gradient" in rules[""][".cmd"]["background"]
    assert rules[""][".asgiven code"]["white-space"] == "pre" and rules[""][".asgiven code"]["overflow-x"] == "auto"
    app = next(sheet for media, sheet in rules.items() if media == "@media screen")
    assert app["html.app .cmdbox:has(> .cp) .cmd"]["padding-right"] == "64px"
    # the inspector's boxes (a run card's recipe) keep the cue on their own background: no rule there sets the background over it
    assert app[".ibody .cmd"] == {"--cmdbg": "var(--bg)"} and not [m for m, sheet in rules.items() for sel, d in sheet.items() if sel.endswith(".cmd") and sel != ".cmd" and "background" in d]
    assert app['.ctl.ico[data-state="dark"] .th-dark']["display"] == "block"
    t = Html(page)
    repro = t.by_id("x-e1:reproduce")
    copy = next(e for e in t.all(repro) if "data-copybtn" in e["attrs"] and e["attrs"]["data-copybtn"].endswith(":recipe"))
    assert t.has(copy["up"], "cmdbox") and not t.of_class("rhd", repro)


def test_an_exports_open_calls_count_calls_and_its_queue_says_needs_judgment(int8_demo, tmp_path):
    """One count, one meaning (the lexicon's D3, the Oct 25 00:00 +08 call): on rb export of the INT8 demo, the page
    the hub serves, the Overview's queue is "Needs judgment" with its row count, and no heading or nav item named
    "Open calls" carries a count other than the open calls (your_call.n). rb open and rb page keep "Needs your
    judgment"."""
    dump = tmp_path / "export.json"
    out = tmp_path / "export"
    r = subprocess.run([*command(), "export", "--out", str(out)], cwd=int8_demo.root, capture_output=True, encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL, env={**int8_demo.env, "RB_TEST_SHELL": "1", "RB_TEST_SHELL_DUMP": str(dump)})
    assert r.returncode == 0, r.stderr
    calls = json.loads(dump.read_text(encoding="utf-8"))["your_call"]["n"]
    t = Html((out / "page.html").read_text(encoding="utf-8"))
    rows = len(t.of_class("qrow", t.by_id("queue")))
    assert rows and said(t.text(t.by_id("h-queue"))) == "Needs judgment"
    assert said(t.text(t.of_class("lede", t.by_id("queue"))[0])) == "Open one to see why it waits and decide it."
    assert said(t.text(t.by_id("h-your-call"))) == f"Open calls ({calls})"
    nav = [said(t.text(e)) for e in t.all(t.by_id("nav")) if e["tag"] == "a" and said(t.text(e)).startswith("Open calls")]
    assert nav == [f"Open calls {calls}"], nav
    # each question's rail counts that question's calls: calls too, so never more than the page's open calls
    named = [(e["attrs"].get("id", ""), said(t.text(e))) for e in t.all() if e["tag"] in ("h1", "h2", "h3") and said(t.text(e)).startswith("Open calls")]
    for id_, s in named:
        m = re.fullmatch(r"Open calls(?: \((\d+)\))?", s)
        assert m and (m.group(1) is None or (int(m.group(1)) == calls if id_ == "h-your-call" else id_.startswith("calls-") and int(m.group(1)) <= calls)), (id_, s)
    assert rows != calls, "the demo's queue holds more than its calls, so the two counts tell apart"


# ---------------------------------------------------------------- the owner's view and the reader's (Oct 25)

_VIEWER_PROBE = r"""(() => {
  const shown = el => !!el && el.getClientRects().length > 0;
  const words = sel => [...document.querySelectorAll(sel)].filter(shown).map(e => e.innerText.replace(/\s+/g, ' ').trim());
  document.querySelectorAll('main details').forEach(d => d.open = true);
  const view = document.getElementById(location.hash.slice(1) || 'overview');
  // an action: an rb command or the Next step's words in a box to copy, a confirmation's Copy, a queue button
  const acts = [...view.querySelectorAll('pre.cmd, [data-copybtn$=":confirm"], .qbtn')].filter(shown)
    .filter(e => !e.matches('pre.cmd') || /^\s*(rb |")/.test(e.textContent));
  const next = [...view.querySelectorAll('h3')].filter(h => shown(h) && h.textContent.trim() === 'Next step');
  return {nav: words('#nav li.yc a'), head: words('#h-queue'), lede: words('#queue .lede'), rows: words('#queue .qrow').length,
    buttons: words('#queue .qbtn').length, callsHead: words('#h-your-call'), callsLede: words('#your-call .lede'),
    actions: acts.length, commands: view.innerText.split('\n').filter(l => /\brb (confirm|decide|reconcile)\b/.test(l)).length,
    next: next.length, nextSaid: next.filter(h => { let e = h.nextElementSibling; while (e && !shown(e)) e = e.nextElementSibling;
      return e && !/^H[1-6]$/.test(e.tagName) && e.innerText.trim().length > 2; }).length};
})()"""


def test_design_pass_1_styles_the_cards_and_views_as_their_writers_asked(record):
    """Design pass 1's CSS requests to R's shell.css (MP-1025-09): C's rule card's parts as a tab row, a big number and a
    source's facts in the UI font with tabular figures (ids, hashes and paths in mono spans), the observation table's
    cells and a rule row's trailing id; H's History times in tabular figures and the day's Raw log line; A's 390 facts
    that never run past the sheet (a 1fr track would take a whole commit as its minimum) and the working view that
    takes the width beside the slim rail."""
    rules = css(page_of(record, example()))
    def rule(sel, media=None):
        for m, sheet in rules.items():
            if sel in sheet and (media is None or media in m):
                return sheet[sel]
        raise AssertionError(f"no rule for {sel}")
    assert "border-bottom" in rule(".secnav.tabs a") and rule(".secnav.tabs a")["border"] == "0"
    for sel in (".big", "dl.rx"):
        assert "var(--sans)" in rule(sel)["font"], sel
    assert rule(".big")["font-variant-numeric"] == "tabular-nums" and rule("dl.rx .mono")["font-family"] == "var(--mono)"
    assert rule("table.obs td.val")["font-variant-numeric"] == "tabular-nums"
    assert rule(".mono.id")["color"] == "var(--dim)" and rule(".hev .ht")["font-variant-numeric"] == "tabular-nums" and rule(".hraw")
    assert rule("dl.rx", "max-width: 639px")["grid-template-columns"] == "minmax(0, 1fr)"
    assert rule("html.app.irail main > *")["max-width"] == "1120px"


def test_the_hub_names_who_reads_the_export_and_it_reads_their_words(int8_demo, tmp_path):
    """OWNER-READER (Oct 25 07:30 +08): the hub injects <meta name="rb-viewer"> before its copy of the script, owner on
    the owner's record address and reader on a share link. The owner's working view says Your call, Needs your
    judgment, that the page gives the command, with every call's command; the reader's says
    Open calls and Still open, that calls are made in a terminal and the page shows where they stand, and in no view
    shows a button, an rb command or a box to copy one from, while every Next step keeps its words; with no meta (an
    export opened from disk) the export keeps its own words, and a viewer the page does not know reads as the reader.
    Read in a real Chrome, at 1440 and 390, every view the navigator opens, so shell.css's rules are what is proved."""
    out = tmp_path / "export"
    r = subprocess.run([*command(), "export", "--out", str(out)], cwd=int8_demo.root, capture_output=True, encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL, env={**int8_demo.env, "RB_TEST_SHELL": "1"})
    assert r.returncode == 0, r.stderr
    page = (out / "page.html").read_text(encoding="utf-8")
    script = (Path(__file__).resolve().parents[1] / "rust/src/shell/assets/script.js").read_text(encoding="utf-8")
    runs = []
    for viewer in ("none", "owner", "reader", "guest"):
        meta = "" if viewer == "none" else f'<meta name="rb-viewer" content="{viewer}">'
        at = tmp_path / f"{viewer}.html"
        at.write_text(page.replace("</head>", f"{meta}<script>{script}</script></head>", 1), encoding="utf-8")
        runs += [[viewer, width, at.as_uri()] for width in (1440, 390)]
    js = _cdp_script(tmp_path / "viewer.mjs", """
const out = {};
await cdp.run(async () => {
  for (const [viewer, width, url] of RUNS) {
    const page = await (await cdp.launchChrome()).page();
    await page.viewport(width, 900);
    await page.goto(url); await cdp.sleep(500);
    const views = await page.ev("[...new Set(['overview', 'your-call'].concat([...document.querySelectorAll('#nav a[href]')].map(a => a.getAttribute('href')).filter(h => h[0] === '#').map(h => h.slice(1))))].filter(v => document.getElementById(v) && document.getElementById(v).matches('.view'))");
    const got = {};
    for (const v of views) { await page.ev(`location.hash = '#${v}'`); await cdp.sleep(250); got[v] = await page.ev(PROBE); }
    out[viewer + '@' + width] = got;
    await cdp.stopAll();
  }
  return {};
}, { timeoutMs: 400000 });
console.log(JSON.stringify({ ok: true, out }));
""".replace("RUNS", json.dumps(runs)).replace("PROBE", json.dumps(_VIEWER_PROBE)))
    code, got, err = _runner(_node(), js, env=dict(os.environ), timeout=420)
    assert code == 0 and got.get("ok"), err[-3000:]
    for width in (1440, 390):
        none, owner, reader, guest = (got["out"][f"{v}@{width}"] for v in ("none", "owner", "reader", "guest"))
        assert any(v.startswith("x-") for v in reader) and any(v.startswith("q-") for v in reader), list(reader)
        ov = {name: g["overview"] for name, g in (("none", none), ("owner", owner), ("reader", reader))}
        n = ov["none"]["rows"]
        assert n and ov["owner"]["rows"] == ov["reader"]["rows"] == n, ov
        assert ov["none"]["head"] == ["Needs judgment"] and ov["none"]["nav"][0].startswith("Open calls") and ov["none"]["buttons"] == 0, ov
        assert ov["none"]["lede"] == ["Open one to see why it waits and decide it."], ov
        assert ov["owner"]["head"] == ["Needs your judgment"] and ov["owner"]["nav"][0].startswith("Your call"), ov
        assert ov["owner"]["lede"] == ["Open one to see why it waits and decide it."], ov
        # each row opens its item for every viewer (the owner's composer is in the item): the export draws no button
        assert ov["owner"]["buttons"] == 0 and owner["your-call"]["callsHead"][0].startswith("Your call ("), owner["your-call"]
        assert ov["reader"]["head"] == ["Still open"] and ov["reader"]["nav"][0].startswith("Open calls"), ov
        assert ov["reader"]["lede"] == ["Not decided yet. Open one to see why it waits."], ov
        assert reader["your-call"]["callsLede"] == ["Calls are made with rb in a terminal, not on this page; it shows where they stand."] and reader["your-call"]["callsHead"][0].startswith("Open calls ("), reader["your-call"]
        # every view: the reader sees no action and no rb command, and every Next step its words; the others see actions
        for v, g in reader.items():
            assert g["actions"] == 0 and g["commands"] == 0 and g["nextSaid"] == g["next"], (v, g)
        assert sum(g["next"] for g in reader.values()) > 0
        for said in (none, owner):
            assert sum(g["actions"] for g in said.values()) > 0 and sum(g["commands"] for g in said.values()) > 0
        # a viewer the page does not know reads as the reader: no action, never the owner's words
        assert guest == reader, (guest, reader)


def test_a_notes_citations_are_chips_on_every_surface(record):
    """CITE (Oct 25 07:20 +08), R's part: a note's "README.md:3 and README.md:7" are two chips, "README.md · line 3" in
    the UI font with the file's name bold, opening the citation's Source view where the page holds it; a log line the
    export may not quote says "Not quoted on a shared page" beside its chip. Built against the fields M's model gives
    (citations.{key}, out/CITE-FIELDS.md in M's notes), injected here, since M's model is not on int yet."""
    doc = example()
    x = doc["experiments"][0]
    x["notes"], x["note_ids"] = ["see README.md:3 and README.md:7; cited logs/agent.log:653-655"], ["nzvl654"]

    def cite(key, path, line, end, nq=""):
        folder, _, name = path.rpartition("/")
        label = f"{path} \u00b7 line {line}" if end == line else f"{path} \u00b7 lines {line}-{end}"
        return {"key": key, "raw": [key], "path": path, "name": name, "folder": folder + "/" if folder else "", "line": line, "end": end,
                "label": label, "missing": "", "not_quoted": nq, "quoted": not nq, "lines": [], "cited_by": [{"kind": "note", "id": "nzvl654"}]}
    doc["citations"] = {"README.md:3": cite("README.md:3", "README.md", 3, 3), "README.md:7": cite("README.md:7", "README.md", 7, 7),
                        "logs/agent.log:653-655": cite("logs/agent.log:653-655", "logs/agent.log", 653, 655, "Not quoted on a shared page")}
    for surface in ("page", "export"):
        t = Html(page_of(record, doc, surface))
        chips = t.of_class("cite", t.by_id(f"x-{x['id']}"))
        assert [said(t.text(c)) for c in chips] == ["README.md \u00b7 line 3", "README.md \u00b7 line 7", "logs/ agent.log \u00b7 lines 653-655"], surface
        assert [t.text(b) for c in chips for b in t.all(c) if b["tag"] == "b"] == ["README.md", "README.md", "agent.log"], surface
        assert [c["attrs"]["data-cite"] for c in chips] == ["README.md:3", "README.md:7", "logs/agent.log:653-655"], surface
        note = chips[2]["up"]
        assert "(Not quoted on a shared page)" in t.text(note) and t.text(note).count("Not quoted") == 1, surface
    # the same in a hypothesis and a rule's statement, where the statement links to its rule: the chip sits beside the
    # link, never inside it (review, should 4)
    x["hypothesis"] = x["hypothesis"] + " (README.md:7)"
    cid = next(iter(doc["findings"]))
    doc["findings"][cid]["statement"] += " (README.md:3)"
    t = Html(page_of(record, doc, "export"))
    xv = t.by_id(f"x-{x['id']}")
    assert sum(c["attrs"]["data-cite"] == "README.md:7" for c in t.of_class("cite", xv)) == 2, t.text(xv)[:600]
    rules = [c for c in t.of_class("cite", t.by_id("criteria")) if c["attrs"]["data-cite"] == "README.md:3"]
    assert rules, t.text(t.by_id("criteria"))[:600]
    for c in t.of_class("cite"):
        up = c["up"]
        while up is not None:
            assert up["tag"] != "a", "a chip inside a link"
            up = up["up"]
    # with no citations in the document, the note's words are drawn as they were
    doc.pop("citations")
    t = Html(page_of(record, doc, "export"))
    assert not t.of_class("cite", t.by_id(f"x-{x['id']}")) and "see README.md:3 and README.md:7" in t.text(t.by_id(f"x-{x['id']}"))


# ---------------------------------------------------------------- design pass 1 (Oct 25)

def test_every_needs_judgment_row_opens_its_item(record):
    """Design pass 1, P11 (the owner: "i can't click onto / into the items listed in 'Needs judgment'"): on rb page and on
    the export, which draws no Review button, each queue row is a link to its item, the whole row following it
    (data-go) with a hover state and the link's keyboard focus: a baseline mismatch its rule's call, a call its call, a
    design its experiment, a changed file nothing cites where it is listed."""
    doc = example()
    for surface in ("page", "export"):
        page = page_of(record, doc, surface)
        t = Html(page)
        rows = t.of_class("qrow", t.by_id("queue"))
        assert rows, surface
        ids = {e["attrs"].get("id") for e in t.all()}
        for r in rows:
            go = r["attrs"].get("data-go")
            assert go and go in ids, (surface, said(t.text(r)))
            assert [e["attrs"]["href"] for e in t.all(r) if t.has(e, "qa")] == [f"#{go}"], (surface, said(t.text(r)))
        kinds = {r["attrs"]["data-kind"]: r["attrs"]["data-go"] for r in rows}
        # two designs wait: the row opens the list that names each (one alone opens its experiment)
        assert [len(e.get("targets", [])) for e in doc["home"]["queue"] if e["kind"] == "design"] == [2]
        assert kinds["baseline"] == "call-c2" and kinds["design"] == "to-confirm" and kinds["uncited"] == "how-made", kinds
    one = copy.deepcopy(doc)
    for e in one["home"]["queue"]:
        if e["kind"] == "design":
            e["targets"] = e["targets"][:1]
    t = Html(page_of(record, one))
    assert next(r for r in t.of_class("qrow", t.by_id("queue")) if r["attrs"]["data-kind"] == "design")["attrs"]["data-go"] == "x-e1"
    rules = css(page)[""]
    assert rules["li.qrow[data-go]"]["cursor"] == "pointer" and "background" in rules["li.qrow[data-go]:hover"]
    # with no script (the export as served) the words' link covers the whole row
    assert rules["li.qrow[data-go]"]["position"] == "relative" and rules["li.qrow[data-go] a.qa::after"]["inset"] == "0"
    assert "outline" in rules["li.qrow a.qa:focus-visible"]


def test_the_about_view_holds_the_boilerplate_once(record):
    """Design pass 1, P10 and target 11: what made the page, rb's honesty line, how to read the record and the words it
    uses sit once, in an About view the top bar links to (on the export, which the hub shows under its own bar, the
    navigator: plain words pass 4), and at the foot of no other view; the terms keep their hovers."""
    doc = example()
    for surface in ("page", "export"):
        t = Html(page_of(record, doc, surface))
        about = t.by_id("about")
        assert about is not None and said(t.text(t.by_id("h-about"))) == "About this record"
        links = {at: [e["attrs"]["href"] for e in t.all(t.by_id(at)) if e["tag"] == "a" and said(t.text(e)) == "About"] for at in ("top", "nav")}
        assert links == ({"top": ["#about"], "nav": []} if surface == "page" else {"top": [], "nav": ["#about"]}), (surface, links)
        feet = [e for e in t.all() if e["tag"] == "footer"]
        assert len(feet) == 1 and t.inside(feet[0], about), surface
        assert t.inside(t.by_id("words"), about) and len([e for e in t.all() if e["attrs"].get("id") == "words"]) == 1
        assert [e for e in t.all() if t.has(e, "term") and not t.inside(e, about)], "the hovers stay on the views"


def test_also_open_in_the_record_is_a_checklist_by_what_is_missing(record):
    """Design pass 1, target 2: "Also open in the record (N)", grouped by what is missing, each group's heading a plain
    sentence with its count, and where it helps what each item needs; each item one short sentence linked to its node,
    its id a trailing chip, never rb's long line or a link-coloured paragraph."""
    doc = example()
    doc["findings"]["c5"]["reading"]["n"] = 1
    doc["open_items"] = [
        {"code": "no_interval", "head": "Criteria with no interval", "n": 1, "under": "", "items": [{"subject": "c5", "code": "no_interval", "what": "c5: 1 unit, rb gives an interval from 3", "units": "", "criteria": ["c5"]}]},
        {"code": "untested", "head": "Criteria nothing tests yet", "n": 1, "under": "", "items": [{"subject": "c6", "code": "untested", "what": "c6 is untested", "units": "", "criteria": ["c6"]}]},
        {"code": "provisional", "head": "Provisional settings", "n": 1, "under": "", "items": [{"subject": "e1/fp32.precision", "code": "provisional",
         "what": "e1/fp32.precision: fp32.precision = fp32 is provisional: no check of it is recorded (source note: FP32 model as trained)", "units": "", "criteria": []}]},
    ]
    doc["findings"]["c6"]["reading"]["n"] = 0
    t = Html(page_of(record, doc))
    items = t.by_id("open-items")
    heads = [said(t.text(e)) for e in t.all(items) if e["tag"] == "h4"]
    assert heads[:3] == ["Rules rb can't judge yet (1). Each needs results from 3 or more runs for an interval.", "Rules with no runs yet (1).",
                         "Settings never checked against a source (1)."]
    rows = [said(t.raw(li)) for li in t.all(items) if li["tag"] == "li"]
    assert rows[:3] == [f"{doc['findings']['c5']['statement']}: 1 design cell so far c5", f"{doc['findings']['c6']['statement']}: no runs yet c6",
                        "fp32.precision in e1: given as fp32, not checked. Its note: FP32 model as trained. e1/fp32.precision"]
    first = next(li for li in t.all(items) if li["tag"] == "li")
    assert [e["attrs"]["href"] for e in t.all(first) if e["tag"] == "a"] == ["#c-c5"] and t.of_class("cid", first)
    assert said(t.text(t.of_class("tt", items)[0])) == "Also open in the record (4)"
    assert css(page_of(record, doc))[""]["ul.oi.chk a.ans"]["color"] == "var(--ink)"



# ---------------------------------------------------------------- plain words pass 4: one bar on the hub (Oct 25)

_BAR_PROBE = r"""(() => {
  const vis = el => !!el && el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0 && getComputedStyle(el).visibility !== 'hidden';
  const top = document.querySelector('header.top'), main = document.querySelector('#main'), title = TITLE;
  const mt = main.getBoundingClientRect().top;
  // every element drawn above the working view's top, or beside it outside the working view and the inspector, whose
  // own text says the record's title
  const repeats = [...document.querySelectorAll('body *')].filter(e => vis(e) && !e.closest('#main, aside.insp')
    && [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.includes(title))).map(e => e.tagName + '.' + e.className);
  const ctl = sel => { const e = document.querySelector('#nav .navctl ' + sel); return vis(e); };
  const vc = document.querySelector('#top .vcrumb');
  const ttl = document.querySelector('#top .ttl');
  return { top: vis(top), bar: vis(top) ? top.innerText.replace(/\s+/g, ' ').trim() : '', slash: vc ? getComputedStyle(vc, '::before').content : '', ttlWidth: vis(ttl) ? Math.round(ttl.getBoundingClientRect().width) : 0, mainTop: Math.round(mt), repeats,
    about: ctl('a[href="#about"]'), theme: ctl('button[data-theme]'), keys: ctl('button[data-keys]'),
    navTabs: [...document.querySelectorAll('#nav a[href]')].filter(vis).length, on: (document.querySelector('main .view.on') || {}).id || '' };
})()"""


def _bar_runs(tmp_path, runs, title: str, steps: str) -> dict:
    """For each (name, url, width) in `runs`: the probe on the Overview, then `steps` (JS on `page`, writing into `got`)."""
    js = _cdp_script(tmp_path / "bar.mjs", """
const out = {};
await cdp.run(async () => {
  for (const [name, url, width] of RUNS) {
    const page = await (await cdp.launchChrome()).page();
    await page.viewport(width, 900);
    await page.goto(url); await cdp.sleep(600);
    const got = { ov: await page.ev(PROBE) };
    STEPS
    out[name] = got;
    await cdp.stopAll();
  }
  return {};
}, { timeoutMs: 300000 });
console.log(JSON.stringify({ ok: true, out }));
""".replace("RUNS", json.dumps(runs)).replace("STEPS", steps).replace("PROBE", json.dumps(_BAR_PROBE.replace("TITLE", json.dumps(title)))))
    code, got, err = _runner(_node(), js, env=dict(os.environ), timeout=320)
    assert code == 0 and got.get("ok"), err[-3000:]
    return got["out"]


def test_inside_the_hub_the_page_draws_no_bar_of_its_own_and_one_bar_elsewhere(int8_demo, tmp_path):
    """Plain words pass 4, one bar on the hub (Oct 25 13:35 +08; the owner: "the headers are redundant visually"). The hub
    serves rb export's page with its rb-viewer meta and its copy of the script, under a bar of its own that names the
    record and the sync time. There, from 1280, the page draws no top bar: at 1440 nothing outside the working view says
    the record's title, and About is in the navigator, opening the About view, which says the record's id and rb's
    version. At 390 the navigator's tabs still open their views, and About stays in reach. With no meta (rb page), one bar: rb's mark and the title, the time small; never the record's id, the
    words "research record" or rb's version, which About says."""
    out = tmp_path / "export"
    r = subprocess.run([*command(), "export", "--out", str(out)], cwd=int8_demo.root, capture_output=True, encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL, env={**int8_demo.env, "RB_TEST_SHELL": "1"})
    assert r.returncode == 0, r.stderr
    export = (out / "page.html").read_text(encoding="utf-8")
    script = (Path(__file__).resolve().parents[1] / "rust/src/shell/assets/script.js").read_text(encoding="utf-8")
    # the record's own title, id, version and time, as the page draws them
    title = html.unescape(re.search(r"<title>(.*?)</title>", export).group(1))
    pid = re.search(r"^id: (\S+)", (int8_demo.root / ".rb/project.yaml").read_text(encoding="utf-8"), re.M).group(1).strip('"')
    version = json.loads(subprocess.run([*command(), "version", "--json"], capture_output=True, encoding="utf-8").stdout)["rb_version"]
    stamp = html.unescape(re.search(r'class="stamp"[^>]*>([^<]*)<', export).group(1))
    runs = []
    for viewer in ("owner", "reader"):
        at = tmp_path / f"hub-{viewer}.html"
        at.write_text(export.replace("</head>", f'<meta name="rb-viewer" content="{viewer}"><script>{script}</script></head>', 1), encoding="utf-8")
        runs += [[f"{viewer}@{w}", at.as_uri(), w] for w in (1440, 390)]
    page = tmp_path / "page.html"
    rp = subprocess.run([*command(), "page", "--out", str(page)], cwd=int8_demo.root, capture_output=True, encoding="utf-8", errors="replace",
                        stdin=subprocess.DEVNULL, env={**int8_demo.env, "RB_TEST_SHELL": "1"})
    assert rp.returncode == 0, rp.stderr
    runs += [["page@1440", page.as_uri(), 1440], ["page@390", page.as_uri(), 390]]
    got = _bar_runs(tmp_path, runs, title, """
    got.aboutView = null;
    if (await page.clickEl("[...document.querySelectorAll('#nav .navctl a[href=\\"#about\\"], header.top a[href=\\"#about\\"]')].find(e => e.getBoundingClientRect().width > 0)")) {
      await cdp.sleep(300);
      got.aboutView = await page.ev("(() => { const v = document.querySelector('main .view.on'); return v ? [v.id, v.innerText] : null; })()");
    }
    await page.clickEl("[...document.querySelectorAll('#nav a[href=\\"#runs\\"]')].find(e => e.getBoundingClientRect().width > 0)");
    await cdp.sleep(300);
    got.runs = await page.ev("(document.querySelector('main .view.on') || {}).id || ''");
""")
    for name, g in got.items():
        ov = g["ov"]
        # every surface: About opens its view, which names the record's id and rb's version, and the tabs open theirs
        assert g["aboutView"] and g["aboutView"][0] == "about" and pid in g["aboutView"][1] and f"rb {version}" in g["aboutView"][1], (name, g["aboutView"])
        assert g["runs"] == "runs" and ov["navTabs"] > 3, (name, g)
        if name.startswith("page"):
            continue
        # inside the hub: About is in the navigator at every width (the script adds the theme and ? beside it, below)
        assert ov["about"], (name, ov)
        if name.endswith("@1440"):
            assert not ov["top"] and ov["mainTop"] == 0 and ov["repeats"] == [], (name, ov)
    for w in (1440, 390):
        ov = got[f"page@{w}"]["ov"]
        assert ov["top"] and ov["bar"].startswith("rb."), ov
        assert pid not in ov["bar"] and "research record" not in ov["bar"].lower() and version not in ov["bar"], ov
    bar = got["page@1440"]["ov"]["bar"]
    assert bar.startswith(f"rb. {title}") and stamp in bar, bar
    # at a phone's width too the one bar says the title, cut short where it must be (review, should 1)
    assert got["page@390"]["ov"]["bar"].startswith(f"rb. {title}") and got["page@390"]["ov"]["ttlWidth"] > 40, got["page@390"]["ov"]


def test_the_one_bar_names_the_view_shown_and_any_viewer_meta_folds_it(int8_demo, tmp_path):
    """Plain words pass 4, one bar, the script's part (A): the bar says rb's mark, the title, then the view's crumb, which
    follows the navigator (Runs after Runs is picked); inside the hub under 1280 the slim bar says the view's name
    alone, and the theme switch and ? sit beside About in the navigator; and rb page with an rb-viewer meta (the preview's reader page) folds its bar at 1440 as the export does, its
    About, theme and ? in the navigator."""
    page = tmp_path / "page.html"
    rp = subprocess.run([*command(), "page", "--out", str(page)], cwd=int8_demo.root, capture_output=True, encoding="utf-8", errors="replace",
                        stdin=subprocess.DEVNULL, env={**int8_demo.env, "RB_TEST_SHELL": "1"})
    assert rp.returncode == 0, rp.stderr
    text = page.read_text(encoding="utf-8")
    title = html.unescape(re.search(r"<title>(.*?)</title>", text).group(1))
    meta = tmp_path / "page-reader.html"
    meta.write_text(text.replace("</head>", '<meta name="rb-viewer" content="reader"></head>', 1), encoding="utf-8")
    out = tmp_path / "export"
    r = subprocess.run([*command(), "export", "--out", str(out)], cwd=int8_demo.root, capture_output=True, encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL, env={**int8_demo.env, "RB_TEST_SHELL": "1"})
    assert r.returncode == 0, r.stderr
    script = (Path(__file__).resolve().parents[1] / "rust/src/shell/assets/script.js").read_text(encoding="utf-8")
    hub = tmp_path / "hub.html"
    hub.write_text((out / "page.html").read_text(encoding="utf-8").replace("</head>", f'<meta name="rb-viewer" content="owner"><script>{script}</script></head>', 1), encoding="utf-8")
    got = _bar_runs(tmp_path, [["page@1440", page.as_uri(), 1440], ["meta@1440", meta.as_uri(), 1440], ["hub@390", hub.as_uri(), 390]], title, """
    await page.clickEl("[...document.querySelectorAll('#nav a[href=\\"#runs\\"]')].find(e => e.getBoundingClientRect().width > 0)");
    await cdp.sleep(300);
    got.runs = await page.ev(PROBE);
""")
    # (the slash between the title and the view's name is the crumb's own, drawn before it)
    p = got["page@1440"]
    assert p["ov"]["bar"].startswith(f"rb. {title} Overview") and p["runs"]["bar"].startswith(f"rb. {title} Runs") and p["ov"]["slash"] == '"/ "', p
    m = got["meta@1440"]["ov"]
    assert not m["top"] and m["about"] and m["theme"] and m["keys"] and m["repeats"] == [], m
    h = got["hub@390"]
    assert h["ov"]["bar"] == "Overview" and h["runs"]["bar"] == "Runs" and h["ov"]["theme"] and h["ov"]["keys"], h


# ---------------------------------------------------------------- PIVOT: the experiment's comparison as a grid


def grids(t: "Html", xid: str) -> list:
    """Each grid of an experiment's comparison: (its title, its column heads, {row head: [cell text, ...]}), and each
    cell's element alongside."""
    out = []
    sec = t.by_id(f"{xid}:cmp")
    title = ""
    for e in t.all(sec):
        if e["tag"] == "h3" and t.has(e, "gt"):
            title = said(t.text(e))
        if e["tag"] == "table" and t.has(e, "grid"):
            thead = next(x for x in t.all(e) if x["tag"] == "thead")
            heads = [said(t.text(h)) for h in t.all(thead) if h["tag"] == "th"]
            rows, cells = {}, {}
            for tr in [x for x in t.all(e) if x["tag"] == "tr"][1:]:
                th = next(x for x in t.all(tr) if x["tag"] == "th")
                tds = [x for x in t.all(tr) if x["tag"] == "td"]
                rows[said(t.text(th))] = [said(t.text(x)) for x in tds]
                cells[said(t.text(th))] = tds
            out.append((title, heads, rows, cells))
            title = ""
    return out


def _cell(mean, n, **more):
    return {"mean": mean, "n": n, "read_by": [], "delta": None, "better": None, "paired": False, "primary": False, "said": "", "basis": "",
            "differs_in": [], "other_code": "", **more}


def _row(variant, role, where, cell):
    return {"variant": variant, "role": role, "varies": {"quant": variant}, "where": where, "metrics": {"top1": cell}}


def test_one_set_of_settings_is_one_table_with_a_column_per_metric(record):
    """No conditions: one table, rows what the experiment varies (FP32 with the one baseline tag), a column per metric;
    a cell says its value and what it is over, and under a variant other than the baseline its difference with it,
    the rule's reading where a rule reads the comparison, linked to that rule. No 'compare within a column' line."""
    doc = example()
    # rb's interval of the change within its column (M's cell fields), which the rule's reading is here
    doc["experiments"][0]["table"]["rows"][1]["metrics"]["top1"].update(lo=-0.0046, hi=-0.0012)
    t = Html(page_of(record, doc))
    [(title, heads, rows, cells)] = grids(t, "x-e1")
    assert title == "" and heads == ["", "Top-1", "ONNX top-1", "Latency (ms)"], heads
    assert list(rows) == ["FP32 baseline", "INT8"] and [x for x in t.of_class("tag", t.by_id("x-e1:cmp"))].__len__() == 1
    assert rows["FP32 baseline"][0] == "0.7597 · 3 seeds", rows
    assert rows["INT8"][0] == "0.7568 · 3 seeds 0.0029 lower than FP32 (-0.0046 to -0.0012)", rows
    link = next(e for e in t.all(cells["INT8"][0]) if e["tag"] == "a" and "lower" in t.text(e))
    assert link["attrs"]["href"] in ("#c-c1", "#cr-c1"), link
    sec = said(t.text(t.by_id("x-e1:cmp")))
    assert "Compare within" not in sec and "From 3 runs." in sec
    assert "n=3" not in sec and "better vs baseline" not in sec


def test_several_sets_are_columns_an_empty_cell_says_not_run(record):
    """Rows the variants, a column per set of the other settings, keys only where a value alone would not read; a cell
    no run fills says Not run; each difference is against the baseline in its own column; the columns line said once."""
    doc = example()
    x = doc["experiments"][0]
    x["table"] = {"metrics": [{"name": "top1", "unit": "%"}], "varies": ["quant"], "conditions": ["calib_images", "scheme"], "rows": [
        _row("fp32", "baseline", {"calib_images": "128", "scheme": "per_tensor"}, _cell(76.13, 3)),
        _row("fp32", "baseline", {"calib_images": "512", "scheme": "per_channel"}, _cell(76.2, 1)),
        _row("int8", "candidate", {"calib_images": "128", "scheme": "per_tensor"}, _cell(75.8, 3, delta=-0.33, paired=True, lo=-0.5, hi=-0.16)),
        _row("w8a16", "candidate", {"calib_images": "512", "scheme": "per_channel"}, _cell(76.1, 1, delta=-0.1, basis="n=1")),
    ]}
    t = Html(page_of(record, doc))
    [(title, heads, rows, _)] = grids(t, "x-e1")
    assert title == "Top-1 (%)" and heads == ["", "Calib images: 128 · Per tensor", "Calib images: 512 · Per channel"], heads
    assert rows["FP32 baseline"] == ["76.13 · 3 seeds", "76.20 · 1 run"], rows
    assert rows["INT8"] == ["75.80 · 3 seeds 0.33 points lower than FP32 (-0.50 to -0.16)", "Not run"], rows
    assert rows["W8a16"] == ["Not run", "76.10 · 1 run 0.10 points lower than FP32 · one run, so no interval"], rows
    sec = said(t.text(t.by_id("x-e1:cmp")))
    assert sec.count("Each column is measured under its own settings. Compare within a column, not across.") == 1


def test_a_difference_whose_units_do_not_pair_says_so(record):
    """An unpaired difference has no interval, and says why in plain words."""
    doc = example()
    x = doc["experiments"][0]
    x["table"] = {"metrics": [{"name": "accuracy", "unit": "percent"}], "varies": ["quant"], "conditions": [], "rows": [
        _row("fp32", "baseline", {}, _cell(13.07, 3)),
        _row("int8", "candidate", {}, _cell(40.6, 2, delta=-2.6, basis="n=2, unpaired")),
    ]}
    for r in x["table"]["rows"]:
        r["metrics"] = {"accuracy": r["metrics"]["top1"]}
    t = Html(page_of(record, doc))
    [(_, heads, rows, _)] = grids(t, "x-e1")
    assert heads == ["", "Accuracy (%)"]
    assert rows["INT8"] == ["40.60 · 2 seeds 2.60 points lower than FP32 · seeds don't match, so no interval"], rows
    assert "lower" in rows["INT8"][0] and "unpaired" not in rows["INT8"][0]


def test_two_variants_under_several_sets_lie_on_their_side(record):
    """FP32 and INT8 under several sets: each set a row, FP32, INT8 and their difference the columns; the baseline's
    own row in each set (FIX2: one row per design cell, as the model gives); the rows line said once."""
    doc = example()
    t = Html(page_of(record, doc))
    [(title, heads, rows, _)] = grids(t, "x-e2")
    assert heads == ["", "FP32 baseline", "INT8", "Difference"], heads
    assert list(rows) == ["Calib images: 128", "Calib images: 512", "Calib images: 1024", "Calib images: 2048"], rows
    assert all(r[0] == rows["Calib images: 128"][0] and r[0].startswith("0.760 · 1 run") for r in rows.values()), rows
    assert rows["Calib images: 128"][1].startswith("0.741 · 1 run") and "lower than FP32" in rows["Calib images: 128"][2], rows
    assert "Each row is measured under its own settings" in said(t.text(t.by_id("x-e2:cmp")))


def test_no_column_mixes_two_values_of_a_setting_that_does_not_vary(record):
    """Whatever the document's rows, each value sits where its row's settings put it: under the column (or, on its side,
    in the row) that names exactly those settings, never beside a value measured under others."""
    doc = example()
    x = doc["experiments"][0]
    rows_ = [
        _row("fp32", "baseline", {"checker": "answer_tag", "problems": "250"}, _cell(13.0667, 3)),
        _row("fp32", "baseline", {"checker": "last_number", "problems": "250"}, _cell(43.2, 1)),
        _row("int8", "candidate", {"checker": "answer_tag", "problems": "250"}, _cell(16.6667, 3, delta=3.6)),
        _row("int8", "candidate", {"checker": "last_number", "problems": "500"}, _cell(42.2, 1)),
        _row("w8a16", "candidate", {"checker": "last_number", "problems": "250"}, _cell(40.6, 2, delta=-2.6, basis="unpaired")),
    ]
    x["table"] = {"metrics": [{"name": "top1", "unit": "%"}], "varies": ["quant"], "conditions": ["checker", "problems"], "rows": rows_}
    t = Html(page_of(record, doc))
    [(_, heads, rows, _)] = grids(t, "x-e1")
    label = {"fp32": "FP32 baseline", "int8": "INT8", "w8a16": "W8a16"}
    words = {("checker", "answer_tag"): "Answer tag", ("checker", "last_number"): "Last number", ("problems", "250"): "250 problems", ("problems", "500"): "500 problems"}
    assert heads[1:] == ["Answer tag · 250 problems", "Last number · 250 problems", "Last number · 500 problems"], heads
    for r in rows_:
        col = " · ".join(words[kv] for kv in r["where"].items())
        assert rows[label[r["variant"]]][heads.index(col) - 1].startswith(f"{r['metrics']['top1']['mean']:.2f} "), (r, rows)
    # every other cell of a row is a set that row was not run under
    filled = sum(1 for cs in rows.values() for c in cs if c != "Not run")
    assert filled == len(rows_)


def test_a_cell_opens_its_runs_and_a_difference_its_rule(record):
    """P12: a cell of one run opens that run's card; of several, the experiment's runs right under the grid; a
    difference opens the rule whose comparison it is. The difference is always the cell's own, within its column: a
    rule that reads the cell otherwise (over other runs) never puts its numbers there."""
    doc = example()
    x = doc["experiments"][0]
    runs = [r for r in x["run_ids"] if not doc["runs"][r]["retracted"]]
    x["table"]["rows"][0]["metrics"]["top1"]["run_ids"] = runs[:2]
    x["table"]["rows"][0]["metrics"]["latency_ms"]["run_ids"] = runs[:1]
    x["table"]["rows"][1]["metrics"]["onnx_top1"]["criteria"] = ["c3"]
    x["table"]["rows"][1]["metrics"]["latency_ms"].pop("run_ids", None)  # a cell as before rb gave run_ids (PV)
    x["table"]["rows"][1]["metrics"]["top1"].update(delta=-0.0031, said="-0.0031", lo=-0.005, hi=-0.0012)
    t = Html(page_of(record, doc))
    [(_, _, rows, cells)] = grids(t, "x-e1")
    # a cell whose runs the document does not list (before rb gave run_ids) opens the runs under the grid too
    unlisted = next(e for e in t.all(cells["INT8"][2]) if e["tag"] == "a")
    assert unlisted["attrs"]["href"] == "#x-e1:runs", unlisted
    many = next(e for e in t.all(cells["FP32 baseline"][0]) if e["tag"] == "a")
    assert many["attrs"]["href"] == "#x-e1:runs" and t.by_id("x-e1:runs") is not None, many
    one = next(e for e in t.all(cells["FP32 baseline"][2]) if e["tag"] == "a")
    assert one["attrs"]["href"] == f"#run-{runs[0]}" or one["attrs"]["href"].endswith(runs[0]), one
    rule = next(e for e in t.all(cells["INT8"][1]) if e["tag"] == "a" and "than FP32" in t.text(e))
    assert rule["attrs"]["href"] in ("#c-c3", "#cr-c3"), rule
    # c1 reads the INT8 top1 cell otherwise (read_by): the cell still says its own change and interval, linked to c1
    assert "0.0031 lower than FP32 (-0.0050 to -0.0012)" in rows["INT8"][0], rows
    link = next(e for e in t.all(cells["INT8"][0]) if e["tag"] == "a" and "than FP32" in t.text(e))
    assert link["attrs"]["href"] in ("#c-c1", "#cr-c1"), link


def test_a_reader_s_queue_rows_say_what_stands_not_an_ask(record):
    """A reader of a shared page is asked to do nothing: each queue row says the state (rb's reader_title), never a verb
    to act on first; the owner and a page with no viewer read the ask."""
    doc = example()
    for i, q in enumerate(doc["home"]["queue"]):
        q["reader_title"] = f"State {i} stands"
    for surface in ("page", "export"):
        page = page_of(record, doc, surface)
        for viewer, first in (("reader", "State"), ("owner", None), ("none", None)):
            t = Html(page, viewer)
            rows = [said(t.reading(e)) for e in t.of_class("qv", t.by_id("queue"))]
            assert rows, surface
            if first:
                assert all(r.startswith(first) for r in rows), (surface, rows)
                assert not [r for r in rows if r.split()[0] in ("Review", "Confirm", "Inspect", "Record", "Choose", "Revisit")], rows
            else:
                assert rows[0] == doc["home"]["queue"][0]["title"], (surface, viewer, rows)


def test_a_difference_keeps_the_rules_digits_where_two_decimals_would_say_another_number(record):
    """A change of +0.05000004 that misses at most 0.05 never reads 0.05: where the screen's digits would say another
    number than the one a primary rule reads, the cell says rb compare's (the rule's) digits; where they agree, the
    screen's."""
    doc = example()
    x = doc["experiments"][0]
    x["table"] = {"metrics": [{"name": "val_loss", "unit": ""}], "varies": ["quant"], "conditions": [], "rows": [
        _row("fp32", "baseline", {}, _cell(1.0, 3)), _row("int8", "candidate", {}, _cell(1.05000004, 3, delta=0.05000004, said="+0.05000004", primary=True)),
        _row("w8a16", "candidate", {}, _cell(4.6, 3, delta=3.6, said="+3.6", primary=True))]}
    for r in x["table"]["rows"]:
        r["metrics"] = {"val_loss": r["metrics"]["top1"]}
    t = Html(page_of(record, doc))
    [(_, _, rows, _)] = grids(t, "x-e1")
    assert "0.05000004 higher than FP32" in rows["INT8"][0], rows
    assert "3.60 higher than FP32" in rows["W8a16"][0], rows


def test_a_difference_says_the_findings_digits_where_they_keep_the_rules_side(record):
    """FIX5: where the cell's rule gives its edge (check_band), the cell says the digits every other view says, more only
    where two decimals would put a number on that edge: e1's "0.16 lower than FP32 (-0.22 to -0.09)" beside at least -1,
    never rb compare's 0.1567; beside at least -0.16, three decimals, -0.157."""
    for target, want in ((-1.0, "0.16 lower than FP32 (-0.22 to -0.09)"), (-0.16, "0.157 lower than FP32 (-0.219 to -0.094)")):
        doc = example()
        doc["findings"]["c1"]["check_band"] = {"comparator": "at least", "target": target, "tolerance": None}
        doc["experiments"][0]["table"]["rows"][1]["metrics"]["top1"].update(delta=-0.1567, said="-0.1567", lo=-0.2192, hi=-0.09415, read_by=[])
        t = Html(page_of(record, doc))
        [(_, _, rows, _)] = grids(t, "x-e1")
        assert want in rows["INT8"][0] and "0.1567" not in rows["INT8"][0], (target, rows["INT8"])
    # a number a float's hair off the rule's edge is on it, as the finding reads it (assess.close): 2 decimals, never 17
    doc = example()
    doc["findings"]["c1"]["check_band"] = {"comparator": "at least", "target": 0.1, "tolerance": None}
    doc["experiments"][0]["table"]["rows"][1]["metrics"]["top1"].update(delta=0.3 - 0.2, said="+0.1", lo=0.3 - 0.2,
                                                                         hi=0.10000000000000002, read_by=[])
    [(_, _, rows, _)] = grids(Html(page_of(record, doc)), "x-e1")
    assert "0.10 higher than FP32 (+0.10 to +0.10)" in rows["INT8"][0], rows["INT8"]


def test_a_variant_that_varies_two_settings_names_each_and_its_difference_the_baseline(record):
    """A row of an experiment that varies two settings names each, with its key where a value alone would not read
    ("FP32 · Calib: 1024"); its difference says "than the baseline", never a name that holds the row's own dots."""
    doc = example()
    x = doc["experiments"][0]
    rows_ = [_row("fp32c1024", "baseline", {}, _cell(0.762, 1)), _row("int8c512", "candidate", {}, _cell(0.745, 1, delta=-0.017, basis="n=1",
                                                                                                        differs_in=["precision", "calib"]))]
    rows_[0]["varies"] = {"precision": "fp32", "calib": "1024"}
    rows_[1]["varies"] = {"precision": "int8", "calib": "512"}
    x["table"] = {"metrics": [{"name": "top1", "unit": ""}], "varies": ["precision", "calib"], "conditions": [], "rows": rows_}
    t = Html(page_of(record, doc))
    [(_, _, rows, _)] = grids(t, "x-e1")
    assert list(rows) == ["FP32 · Calib: 1024 baseline", "INT8 · Calib: 512"], rows
    assert rows["INT8 · Calib: 512"] == ["0.745 · 1 run 0.017 lower than the baseline · one run, so no interval · also differs in precision and calib"], rows


def test_a_difference_is_never_said_where_its_column_has_no_baseline(record):
    """A change rb gives against a baseline the column does not hold (its runs under other settings) is not said there:
    a difference never crosses columns."""
    doc = example()
    x = doc["experiments"][0]
    x["table"] = {"metrics": [{"name": "top1", "unit": "%"}], "varies": ["quant"], "conditions": ["data"], "rows": [
        _row("fp32", "baseline", {"data": "old"}, _cell(76.0, 3)), _row("int8", "candidate", {"data": "new"}, _cell(75.0, 3, delta=-1.0)),
        _row("w8a16", "candidate", {"data": "old"}, _cell(76.0, 3, delta=0.0))]}
    t = Html(page_of(record, doc))
    [(_, heads, rows, _)] = grids(t, "x-e1")
    assert heads == ["", "Old", "New"] and rows["INT8"] == ["Not run", "75.00 · 3 seeds"], rows
    assert rows["W8a16"] == ["76.00 · 3 seeds level with FP32", "Not run"], rows
