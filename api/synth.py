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

This module is TRANSPORT-FREE. app.py owns routing and sockets; everything here
is a pure function, which is why swapping Vercel's per-file handler model for a
single WSGI entrypoint changed nothing below this line.
    {"contract": 1, "ok": false, "code": "...", "reason": "...", "log": "..."}

EVERY failure is a 200 with ok:false and a NAMED code, except a malformed request
(400) and an unexpected crash (500). A design that does not compile is not an
HTTP error — it is an answer, and the client shows it to the user as their own
mistake rather than "try again later".
"""
import json

from ._flow import FlowError, synthesise
from ._licence import screen

CONTRACT_VERSION = 1
MAX_BYTES = 2 * 1024 * 1024        # generous for HDL, small enough to bound abuse
MAX_FILES = 64


def _refuse(code, reason, **extra):
    return {"contract": CONTRACT_VERSION, "ok": False, "code": code, "reason": reason, **extra}


def handle_synth(raw_body, *, run=None):
    """The whole request path, as a pure function: bytes in, (status, body) out.

    SEPARATED FROM THE TRANSPORT ON PURPOSE. Everything that can be wrong with a
    request — a bad contract version, a copyleft source, too many files — is
    decided here, where it can be tested without a socket, a server or a deploy.
    The class below only moves bytes.

    `run` is the synthesis function, injected so the request path can be
    exercised without a ~300 MB toolchain. That matters more than it sounds:
    these handlers had never served a single request when they were written, and
    a test needing the real flow would have kept it that way.
    """
    do_run = run or synthesise

    if raw_body is None or len(raw_body) == 0:
        return 400, _refuse("bad-request", "The request body is empty.")
    if len(raw_body) > MAX_BYTES:
        return 400, _refuse("bad-request",
                            f"The request must be at most {MAX_BYTES} bytes.")

    try:
        req = json.loads(raw_body)
    except Exception as e:                      # noqa: BLE001
        return 400, _refuse("bad-request", f"Body was not JSON: {e}")
    if not isinstance(req, dict):
        return 400, _refuse("bad-request", "The body must be a JSON object.")

    if req.get("contract") != CONTRACT_VERSION:
        return 200, _refuse(
            "contract-mismatch",
            f"This service speaks contract {CONTRACT_VERSION}; the request said "
            f"{req.get('contract')!r}. Refusing rather than guessing at the difference.")

    files = req.get("files") or []
    if not isinstance(files, list) or not files:
        return 200, _refuse("no-sources", "There is nothing to synthesise.")
    if len(files) > MAX_FILES:
        return 200, _refuse("too-many-files", f"At most {MAX_FILES} files.")

    # The rule that keeps this service out of GPL distribution. The client
    # screens before upload for the user's sake; this screens again because a
    # client can be bypassed and the rule is not a UX nicety. It runs BEFORE
    # any synthesis, so a refused source is never compiled even briefly.
    refusals, warnings = screen(files)
    if refusals:
        return 200, _refuse(
            "source-licence-refused",
            "One or more sources may not be built on this service.",
            refusals=refusals, warnings=warnings, alternative="local-tier")

    try:
        result = do_run(files, req.get("constraints", ""), req.get("top"),
                        req.get("target") or {})
    except FlowError as e:
        return 200, _refuse(e.code, e.reason, log=e.log, warnings=warnings)
    except Exception as e:                      # noqa: BLE001
        return 500, _refuse("internal-error", f"Unexpected failure: {e}")

    return 200, {
        "contract": CONTRACT_VERSION, "ok": True,
        "netlist": result["netlist"],
        "bitstream": result["bitstream_b64"],
        "log": result["log"],
        "toolVersions": result["toolVersions"],
        "warnings": warnings}
