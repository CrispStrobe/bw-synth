"""WSGI entrypoint — one app, two routes.

WHY THIS SHAPE. Vercel's Python runtime wants a single declared entrypoint
(`[tool.vercel] entrypoint` in pyproject.toml) rather than one function per
`api/*.py`. The first deploy said so:

    No python entrypoint found in default locations, but found potential
    entrypoints: api/health.py (variable: handler), api/synth.py (...)

So the transport is a plain WSGI app that dispatches on PATH_INFO. No framework
and no new dependency: `requirements.txt` stays the three tools, which keeps the
licence inventory in this repository exactly as long as it was.

THE POINT THIS PROVED. `handle_synth` is a pure function — bytes in,
(status, body) out — and the licence screen knows nothing about HTTP. Changing
the entire transport model therefore touched neither, and their 18 tests did not
move. That separation was made because the handlers had never served a request;
it paid off the first time the platform disagreed with their shape.
"""
import json

from api._flow import tool_versions
from api.synth import CONTRACT_VERSION, MAX_BYTES, handle_synth

JSON_HEADERS = [("content-type", "application/json")]


def _response(start_response, status_code, body):
    payload = json.dumps(body).encode("utf-8")
    reason = {200: "OK", 400: "Bad Request", 404: "Not Found",
              405: "Method Not Allowed", 500: "Internal Server Error",
              503: "Service Unavailable"}.get(status_code, "OK")
    headers = JSON_HEADERS + [("content-length", str(len(payload)))]
    start_response(f"{status_code} {reason}", headers)
    return [payload]


def _health():
    """The probe brickwright-lite's backend selector calls.

    503 when the toolchain is not genuinely present. The selector is fail-closed
    — a backend that does not answer is not offered — so a service that cannot
    do the work must say so rather than accept it and fail later.
    """
    versions = tool_versions()
    ok = all(not str(v).startswith("unavailable") for v in versions.values())
    return (200 if ok else 503), {"contract": CONTRACT_VERSION, "ok": ok,
                                  "toolVersions": versions}


def app(environ, start_response):
    path = (environ.get("PATH_INFO") or "/").rstrip("/") or "/"
    method = environ.get("REQUEST_METHOD", "GET").upper()

    if path in ("/api/health", "/health"):
        if method != "GET":
            return _response(start_response, 405,
                             {"contract": CONTRACT_VERSION, "ok": False,
                              "code": "method-not-allowed", "reason": "Health is a GET."})
        status, body = _health()
        return _response(start_response, status, body)

    if path in ("/api/synth", "/synth", "/"):
        if method != "POST":
            return _response(start_response, 405,
                             {"contract": CONTRACT_VERSION, "ok": False,
                              "code": "method-not-allowed", "reason": "Synthesis is a POST."})
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BYTES:
            return _response(start_response, 400,
                             {"contract": CONTRACT_VERSION, "ok": False,
                              "code": "bad-request",
                              "reason": f"content-length must be 0..{MAX_BYTES}."})
        body_bytes = environ["wsgi.input"].read(length) if length else b""
        status, body = handle_synth(body_bytes)
        return _response(start_response, status, body)

    return _response(start_response, 404,
                     {"contract": CONTRACT_VERSION, "ok": False, "code": "no-such-route",
                      "reason": f"No route for {path}. Try /api/synth or /api/health."})
