#!/usr/bin/env python3
"""A local stand-in for the hosted service, for trying share_beat.mjs where there is none (the box): it speaks what
rb sync asks of a hub (who the credential is, then each private upload, answered with a link on 127.0.0.1) and serves
the share link, /s/demo, as the hub serves one: a browser that opens it (or its /read) as a page (Sec-Fetch-Dest:
document, or none) gets the hub's own page, a bar ("Shared, read-only", when it was synced, "Made with rb") over rb's page
in a sandboxed frame, and only the frame's own request (Sec-Fetch-Dest: iframe) gets the latest upload's page. As the hub
does (HOSTED-VIEW.md, 4), a page whose <html> carries data-shell (rb export strips its script and names it there) gets
the host's own copy of rb's shell script inline, allowed by its hash alone, so the page draws rb's three panes; the copy is
rust/src/shell/assets/script.js beside this tool (--shell-script names another). It keeps what came (when, and its bytes'
length).

    python tools/demo/share_standin.py [--port N] [--shell-script <script.js>]      # prints the hub's address and the share link, then serves

With it: RB_HUB_TOKEN=rb_demo_local node tools/demo/share_beat.mjs ... --hub <address> --link <address>/s/demo
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import io
import json
import sys
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# the frame as the hub draws it: no allow-same-origin, so the frame's origin is opaque and the page around it cannot read
# its words (a reader's browser, and the share beat, reads them inside the frame)
SANDBOX = "allow-scripts allow-popups allow-popups-to-escape-sandbox"
# the host's copy of rb's shell script: the one this tool's own repository builds rb with
SHELL_SCRIPT = Path(__file__).resolve().parents[2] / "rust" / "src" / "shell" / "assets" / "script.js"


def hosted(page: bytes, script: bytes) -> tuple[bytes, str | None]:
    """The page as the hub serves it in its frame, and the Content-Security-Policy it goes with: a page with data-shell on
    its <html> gets `script` inline at the end of its <head> (the shell marks the page before its body parses) and the
    policy hub staging sends with it (read Oct 25): the frame's sandbox again, nothing loaded but that script by its hash,
    its own inline styles and data: images, no network, forms or base; any other page goes as it came, in document mode."""
    head = page[:page.find(b">", page.find(b"<html")) + 1] if b"<html" in page else b""
    if b" data-shell=" not in head or b"</head>" not in page:
        return page, None
    at = page.find(b"</head>")
    digest = base64.b64encode(hashlib.sha256(script).digest()).decode()
    policy = (f"sandbox {SANDBOX}; default-src 'none'; script-src 'sha256-{digest}'; connect-src 'none'; "
              "style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'; base-uri 'none'; form-action 'none'")
    return page[:at] + b"<script>" + script + b"</script>" + page[at:], policy


def frame_page(title: str, synced: float) -> bytes:
    """The hub's page for a share link: its bar (HOSTED-VIEW.md, 2: rb., the record's title, Shared, read-only, when it
    was synced, Made with rb; rb's page in hosted mode leaves the record's name to it), and rb's page in a frame of its
    own."""
    when = time.strftime("%Y-%m-%d %H:%M", time.gmtime(synced))
    return ("<!doctype html><html><head><meta charset=\"utf-8\"><title>" + html.escape(title) + "</title><style>"
            "html,body{margin:0;height:100%;font:14px system-ui,sans-serif}body{display:flex;flex-direction:column}"
            ".bar{display:flex;gap:16px;padding:8px 14px;border-bottom:1px solid #ddd;background:#fafafa;color:#333}"
            ".bar .end{margin-left:auto}.view-frame{flex:1;border:0;width:100%}</style></head><body>"
            "<div class=\"bar\"><b>rb.</b><b>" + html.escape(title) + "</b><span>Shared, read-only</span><span>synced " + when + " UTC</span>"
            "<a class=\"end\" href=\"https://rabbitbrain.ai\">Made with rb</a></div>"
            f"<iframe src=\"/s/demo/read\" sandbox=\"{SANDBOX}\" title=\"{html.escape(title)}\" class=\"view-frame\"></iframe>"
            "</body></html>").encode()


class StandIn:
    def __init__(self, port: int = 0, shell_script: Path = SHELL_SCRIPT):
        script = shell_script.read_bytes()
        got = self.got = []
        pages = self.pages = []

        class H(BaseHTTPRequestHandler):
            def _send(self, code, body: bytes, kind: str, policy: str | None = None):
                self.send_response(code)
                self.send_header("content-type", kind)
                if policy:
                    self.send_header("content-security-policy", policy)
                self.send_header("content-length", str(len(body)))
                self.send_header("cache-control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code, obj):
                self._send(code, json.dumps(obj).encode(), "application/json")

            def do_GET(self):
                origin = f"http://127.0.0.1:{self.server.server_address[1]}"
                if self.path == "/api/hub/whoami":
                    return self._json(200, {"handle": "ana", "base": f"{origin}/u/ana", "publish": f"{origin}/api/hub/investigations"})
                if self.path.split("?")[0] in ("/s/demo", "/s/demo/read"):
                    if not pages:
                        return self._send(404, b"<!doctype html><p>Nothing synced yet.</p>", "text/html; charset=utf-8")
                    if self.headers.get("sec-fetch-dest") == "iframe":
                        page, policy = hosted(pages[-1], script)
                        return self._send(200, page, "text/html; charset=utf-8", policy)
                    return self._send(200, frame_page(got[-1]["title"], got[-1]["at"]), "text/html; charset=utf-8")
                self._json(404, {"error": "not here"})

            def do_PUT(self):
                body = self.rfile.read(int(self.headers.get("content-length", "0")))
                with zipfile.ZipFile(io.BytesIO(body)) as z:
                    names = z.namelist()
                    man = json.loads(z.read(next(n for n in names if n.endswith("manifest.json"))))
                    page = z.read(next(n for n in names if n.endswith("page.html")))
                got.append({"at": round(time.time(), 3), "bytes": len(body), "project": man["project"]["id"],
                            "title": man["project"].get("title") or man["project"]["id"]})
                pages.append(page)  # after its line, so a page served is never one its bar has no line for
                self._json(201, {"url": f"http://127.0.0.1:{self.server.server_address[1]}/u/ana/{man['project']['id']}"})

            def log_message(self, *x):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.link = f"{self.url}/s/demo"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--shell-script", type=Path, default=SHELL_SCRIPT)
    a = ap.parse_args(argv)
    s = StandIn(a.port, a.shell_script)
    print(json.dumps({"hub": s.url, "link": s.link}), flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
