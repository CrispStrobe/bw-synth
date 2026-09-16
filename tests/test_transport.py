"""The WSGI app — routing and bytes, and nothing else.

`handle_synth` is tested thoroughly and knows nothing about HTTP. This covers the
thin layer above it, driven through the WSGI interface directly: no socket, no
server, no toolchain.

It is thin ON PURPOSE and this suite exists to keep it that way. The test that
matters most asserts the transport DECIDES NOTHING — its output must equal what
handle_synth produced for the same bytes. A transport that starts making
decisions is one whose decisions nobody tests, and it drifts there one
convenience at a time.

What remains unverifiable here: whether Vercel's runtime calls this app the way
WSGI specifies. Only a deploy answers that.
"""
import io
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app import app                                                # noqa: E402
from api.synth import CONTRACT_VERSION, handle_synth               # noqa: E402

MIT = "// SPDX-License-Identifier: MIT\nmodule blink(output led); assign led=1'b1; endmodule"
GPL = {"contract": CONTRACT_VERSION,
       "files": [{"name": "c.v", "source": "// SPDX-License-Identifier: GPL-3.0-only"}]}
GOOD = {"contract": CONTRACT_VERSION, "files": [{"name": "b.v", "source": MIT}]}


def call(path, method="POST", body=None, content_length=None):
    raw = b"" if body is None else (
        body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode())
    environ = {
        "PATH_INFO": path,
        "REQUEST_METHOD": method,
        "CONTENT_LENGTH": str(len(raw)) if content_length is None else content_length,
        "wsgi.input": io.BytesIO(raw),
    }
    captured = {}

    def start_response(status, headers):
        captured["status"] = int(status.split()[0])
        captured["headers"] = {k.lower(): v for k, v in headers}

    chunks = app(environ, start_response)
    payload = b"".join(chunks)
    return captured, (json.loads(payload) if payload else None), payload


def test_synth_route_returns_json_with_a_declared_length():
    cap, body, payload = call("/api/synth", body=GPL)
    assert cap["status"] == 200
    assert cap["headers"]["content-type"] == "application/json"
    assert int(cap["headers"]["content-length"]) == len(payload), \
        "a declared length that disagrees with the body truncates the client's read"
    assert body["code"] == "source-licence-refused"


def test_the_transport_decides_nothing():
    # The whole point of the split, and the reason changing Vercel's handler
    # model cost nothing below this line.
    raw = json.dumps(GPL).encode()
    expected_status, expected_body = handle_synth(raw, run=lambda *a, **k: None)
    cap, body, _ = call("/api/synth", body=GPL)
    assert cap["status"] == expected_status
    assert body == expected_body


def test_health_reports_the_toolchain_and_fails_closed_without_it():
    cap, body, _ = call("/api/health", method="GET")
    # No toolchain on this machine, so it must say so rather than claim to work.
    assert cap["status"] in (200, 503)
    assert body["contract"] == CONTRACT_VERSION
    assert isinstance(body["toolVersions"], dict)
    assert body["ok"] is (cap["status"] == 200)


def test_wrong_methods_are_405_by_name():
    assert call("/api/synth", method="GET")[1]["code"] == "method-not-allowed"
    assert call("/api/health", method="POST")[1]["code"] == "method-not-allowed"


def test_an_unknown_route_is_404_and_says_what_exists():
    cap, body, _ = call("/api/nope", method="GET")
    assert cap["status"] == 404
    assert "api/synth" in body["reason"]


def test_a_bad_content_length_is_400_not_a_crash():
    cap, body, _ = call("/api/synth", body=GOOD, content_length="not a number")
    assert cap["status"] == 400
    assert body["code"] == "bad-request"


def test_an_oversized_body_is_refused_before_it_is_read():
    cap, body, _ = call("/api/synth", body=GOOD, content_length=str(3 * 1024 * 1024))
    assert cap["status"] == 400
