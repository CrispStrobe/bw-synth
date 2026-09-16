"""POST /api/synth — contract v1.

The contract is defined by brickwright-lite's client
(overlay/scratch-gui/src/lib/bw-fpga/synthesis.js) and restated here so the two
can be checked against each other without either being "the" copy.

  REQUEST
    {"contract": 1,
     "target": {"family": "GW2A", "device": "GW2AR-LV18QN88C8/I7", "vopt": "family"},
     "top": "blink",
     "files": [{"name": "blink.v", "source": "..."}],
     "constraints": "IO_LOC \\"led\\" 73;\\n..."}

  RESPONSE 200
    {"contract": 1, "ok": true, "netlist": {...}, "bitstream": "<base64>|null",
     "log": "...", "toolVersions": {...}}
    {"contract": 1, "ok": false, "code": "...", "reason": "...", "log": "..."}

EVERY failure is a 200 with ok:false and a NAMED code, except a malformed request
(400) and an unexpected crash (500). A design that does not compile is not an
HTTP error — it is an answer, and the client shows it to the user as their own
mistake rather than "try again later".
"""
import json
from http.server import BaseHTTPRequestHandler

from ._flow import FlowError, synthesise
from ._licence import screen

CONTRACT_VERSION = 1
MAX_BYTES = 2 * 1024 * 1024        # generous for HDL, small enough to bound abuse
MAX_FILES = 64


def _reply(handler, status, body):
    payload = json.dumps(body).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json")
    handler.send_header("content-length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def _refuse(code, reason, **extra):
    return {"contract": CONTRACT_VERSION, "ok": False, "code": code, "reason": reason, **extra}


class handler(BaseHTTPRequestHandler):          # noqa: N801 — Vercel's expected name
    def do_POST(self):                          # noqa: N802
        try:
            length = int(self.headers.get("content-length") or 0)
        except ValueError:
            return _reply(self, 400, _refuse("bad-request", "Unreadable content-length."))
        if length <= 0 or length > MAX_BYTES:
            return _reply(self, 400, _refuse(
                "bad-request", f"The request must be between 1 and {MAX_BYTES} bytes."))

        try:
            req = json.loads(self.rfile.read(length))
        except Exception as e:                  # noqa: BLE001
            return _reply(self, 400, _refuse("bad-request", f"Body was not JSON: {e}"))

        if req.get("contract") != CONTRACT_VERSION:
            return _reply(self, 200, _refuse(
                "contract-mismatch",
                f"This service speaks contract {CONTRACT_VERSION}; the request said "
                f"{req.get('contract')!r}. Refusing rather than guessing at the difference."))

        files = req.get("files") or []
        if not isinstance(files, list) or not files:
            return _reply(self, 200, _refuse("no-sources", "There is nothing to synthesise."))
        if len(files) > MAX_FILES:
            return _reply(self, 200, _refuse("too-many-files",
                                             f"At most {MAX_FILES} files."))

        # The rule that keeps this service out of GPL distribution. The client
        # screens before upload for the user's sake; this screens again because a
        # client can be bypassed and the rule is not a UX nicety.
        refusals, warnings = screen(files)
        if refusals:
            return _reply(self, 200, _refuse(
                "source-licence-refused",
                "One or more sources may not be built on this service.",
                refusals=refusals, warnings=warnings, alternative="local-tier"))

        target = req.get("target") or {}
        try:
            result = synthesise(files, req.get("constraints", ""), req.get("top"), target)
        except FlowError as e:
            return _reply(self, 200, _refuse(e.code, e.reason, log=e.log, warnings=warnings))
        except Exception as e:                  # noqa: BLE001
            return _reply(self, 500, _refuse("internal-error", f"Unexpected failure: {e}"))

        return _reply(self, 200, {
            "contract": CONTRACT_VERSION, "ok": True,
            "netlist": result["netlist"],
            "bitstream": result["bitstream_b64"],
            "log": result["log"],
            "toolVersions": result["toolVersions"],
            "warnings": warnings})

    def do_GET(self):                           # noqa: N802
        _reply(self, 405, _refuse("method-not-allowed", "Synthesis is a POST."))
