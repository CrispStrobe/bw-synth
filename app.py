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
import os

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


def _environment():
    """Facts about the runtime, reported when the toolchain cannot be found.

    Added after a live deployment insisted every tool was missing while the build
    log said "Installing required dependencies from pyproject.toml...". Both
    cannot be true, and the difference between "not installed", "installed
    elsewhere" and "too large to ship" needs evidence rather than another guess —
    each has a different fix and I had already spent three rounds guessing at
    strings on this project.
    """
    import importlib.util
    import site
    import sys as _sys

    modules = {}
    for name in ("yowasp_yosys", "yowasp_nextpnr_himbaechel_gowin", "apycula"):
        try:
            spec = importlib.util.find_spec(name)
            modules[name] = spec.origin if spec else "not found on sys.path"
        except Exception as e:                  # noqa: BLE001
            modules[name] = f"error: {type(e).__name__}: {e}"

    try:
        site_packages = site.getsitepackages()
    except Exception:                           # noqa: BLE001
        site_packages = []

    # Ephemeral disk, because that is what actually stopped this working.
    # The dependencies install into /tmp AND the YoWASP packages unpack their
    # WebAssembly into /tmp at first run, and a Lambda's /tmp is small. A number
    # here turns "no space left on device" into a decision about the platform.
    disk = {}
    for path in ("/tmp", "/var/task"):
        try:
            st = os.statvfs(path)
            disk[path] = {
                "totalMB": round(st.f_blocks * st.f_frsize / 1048576),
                "freeMB": round(st.f_bavail * st.f_frsize / 1048576),
            }
        except Exception as e:                  # noqa: BLE001
            disk[path] = f"unavailable: {e}"
    try:
        deps = "/tmp/_vc_deps"
        total = sum(os.path.getsize(os.path.join(r, f))
                    for r, _, fs in os.walk(deps) for f in fs
                    if os.path.exists(os.path.join(r, f)))
        disk["depsMB"] = round(total / 1048576)
    except Exception as e:                      # noqa: BLE001
        disk["depsMB"] = f"unavailable: {e}"

    return {
        "disk": disk,
        "python": _sys.version.split()[0],
        "executable": _sys.executable,
        "sysPathTail": _sys.path[-6:],
        "sitePackages": site_packages[:3],
        "modules": modules,
    }


def _health():
    """The probe brickwright-lite's backend selector calls.

    503 when the toolchain is not genuinely present. The selector is fail-closed
    — a backend that does not answer is not offered — so a service that cannot
    do the work must say so rather than accept it and fail later.
    """
    versions = tool_versions()
    # `unavailable:` is the only shape tool_versions() reports for a tool that
    # could not be RUN — resolution proves execution now, so this cannot report
    # healthy while a tool is broken. An earlier version keyed on the same prefix
    # while resolution only checked importability, and cheerfully returned
    # ok:true with two tools failing.
    ok = all(not str(v).startswith("unavailable") for v in versions.values())
    body = {"contract": CONTRACT_VERSION, "ok": ok, "toolVersions": versions}
    if not ok:
        # Only when something is wrong: a healthy probe should be small and
        # boring, and this is diagnostic detail nobody needs when it works.
        body["environment"] = _environment()
    return (200 if ok else 503), body


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
