"""The handler class itself — the bytes-moving layer under the request path.

`handle_synth` is tested thoroughly and knows nothing about HTTP. This covers the
thin part above it: reading content-length, passing the body through, and writing
a well-formed response. It is thin ON PURPOSE, and this suite exists to confirm
it stayed thin — a transport that starts making decisions is one whose decisions
nobody tests.

Exercised by driving the handler with fake streams rather than opening a socket.
That leaves exactly one thing unverified — whether Vercel's Python runtime
invokes this class the way `BaseHTTPRequestHandler` expects — and only a deploy
can answer that.
"""
import io
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from api.synth import CONTRACT_VERSION, handler                    # noqa: E402

MIT = "// SPDX-License-Identifier: MIT\nmodule blink(output led); assign led=1'b1; endmodule"


class _Recorder(handler):
    """A handler with its socket replaced, so do_POST can be called directly."""

    def __init__(self, body: bytes, headers=None):                  # noqa: D107
        # Deliberately NOT calling super().__init__: BaseHTTPRequestHandler's
        # constructor takes a live connection and runs the request loop.
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.headers = headers if headers is not None else {"content-length": str(len(body))}
        self.status = None
        self.sent_headers = {}

    def send_response(self, code, message=None):
        self.status = code

    def send_header(self, key, value):
        self.sent_headers[key.lower()] = value

    def end_headers(self):
        pass


def _post(body_obj, headers=None):
    raw = body_obj if isinstance(body_obj, (bytes, bytearray)) else json.dumps(body_obj).encode()
    h = _Recorder(raw, headers)
    h.do_POST()
    written = h.wfile.getvalue()
    return h, (json.loads(written) if written else None)


GOOD = {"contract": CONTRACT_VERSION, "top": "blink",
        "files": [{"name": "blink.v", "source": MIT}], "constraints": ""}

GPL = {"contract": CONTRACT_VERSION,
       "files": [{"name": "c.v", "source": "// SPDX-License-Identifier: GPL-3.0-only"}]}


def test_a_refusal_is_written_as_json_with_the_right_status():
    # No toolchain here, so a good request reaches synthesis and fails — which is
    # fine: what is under test is that the RESPONSE is well formed, not that
    # synthesis worked.
    h, body = _post(GPL)
    assert h.status == 200
    assert h.sent_headers["content-type"] == "application/json"
    assert body["ok"] is False
    assert body["code"] == "source-licence-refused"


def test_content_length_is_honoured_and_declared():
    h, body = _post(GPL)
    assert int(h.sent_headers["content-length"]) == len(h.wfile.getvalue()), \
        "a declared length that disagrees with the body truncates the client's read"


def test_a_missing_content_length_is_a_400_not_a_crash():
    h, body = _post(GOOD, headers={})
    assert h.status == 400
    assert body["code"] == "bad-request"


def test_an_unreadable_content_length_is_a_400_not_a_crash():
    h, body = _post(GOOD, headers={"content-length": "not a number"})
    assert h.status == 400


def test_the_transport_makes_no_decisions_of_its_own():
    # The point of the split. If the transport ever starts deciding things, its
    # decisions are the ones nobody tests — so it must produce exactly what
    # handle_synth produced.
    from api.synth import handle_synth
    raw = json.dumps(GPL).encode()
    expected_status, expected_body = handle_synth(raw, run=lambda *a, **k: None)
    h, body = _post(GPL)
    assert h.status == expected_status
    assert body == expected_body


def test_GET_is_refused_by_name():
    h = _Recorder(b"")
    h.do_GET()
    assert h.status == 405
    assert json.loads(h.wfile.getvalue())["code"] == "method-not-allowed"
