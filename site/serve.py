#!/usr/bin/env python3
"""Local dev server; chdir first so it works regardless of launch cwd.

Sends `Cache-Control: no-store` on everything so the browser always picks up
the latest app.js / style.css / results.db without a hard refresh - the
GitHub Pages deploy does its own `?v=` cache-busting (see the workflow), so
this header only affects local development.
"""
import os
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler

os.chdir(os.path.dirname(os.path.abspath(__file__)))


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8643
    HTTPServer(("127.0.0.1", port), NoCacheHandler).serve_forever()
