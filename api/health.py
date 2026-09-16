"""GET /api/health — the probe brickwright-lite's backend selector calls.

It answers only if the toolchain is genuinely present. The selector is
fail-closed: a backend that does not answer is not offered, and a picker entry
nobody can select is a lie the front end tells for us. So an unhealthy service
must say so rather than 200 and fail at synthesis time.
"""
import json
from http.server import BaseHTTPRequestHandler

from ._flow import tool_versions


class handler(BaseHTTPRequestHandler):          # noqa: N801
    def do_GET(self):                           # noqa: N802
        versions = tool_versions()
        ok = all(not v.startswith("unavailable") for v in versions.values())
        payload = json.dumps({"contract": 1, "ok": ok, "toolVersions": versions}).encode()
        self.send_response(200 if ok else 503)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
