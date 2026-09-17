"""The request path, exercised without a socket, a server or a toolchain.

These handlers had never served a single request when they were written — the
smoke test proves the FLOW, and proved nothing about what happens to a request
before it reaches the flow. That gap is where a contract mismatch, a licence
refusal or a malformed body would have gone unnoticed until a deploy.

The synthesis function is injected, so every one of these runs in milliseconds
and none needs the ~300 MB toolchain. A test that needed it would have been the
reason this path stayed untested.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from api.synth import CONTRACT_VERSION, handle_synth                # noqa: E402
from api._flow import FlowError                                     # noqa: E402

MIT = "// SPDX-License-Identifier: MIT\nmodule blink(output led); assign led=1'b1; endmodule"
GPL = "// SPDX-License-Identifier: GPL-3.0-only\nmodule c; endmodule"

FAKE_RESULT = {"netlist": {"modules": {"blink": {"ports": {"led": {}}}}},
               "sim_netlist": {"modules": {"blink": {"ports": {"led": {}}, "cells": {}}}},
               "bitstream_b64": "QUJD", "log": "ok", "toolVersions": {"yosys": "test"}}


def _post(body, run=None):
    raw = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode()
    return handle_synth(raw, run=run or (lambda *a, **k: FAKE_RESULT))


def _req(**over):
    base = {"contract": CONTRACT_VERSION, "top": "blink",
            "files": [{"name": "blink.v", "source": MIT}],
            "constraints": 'IO_LOC "led" 73;',
            "target": {"family": "GW2A-18C", "device": "GW2AR-LV18QN88C8/I7"}}
    base.update(over)
    return base


def test_a_good_request_round_trips():
    status, body = _post(_req())
    assert status == 200
    assert body["ok"] is True
    assert body["contract"] == CONTRACT_VERSION
    assert body["netlist"]["modules"]["blink"]
    assert body["bitstream"] == "QUJD"
    # The generic sim netlist rides alongside the bitstream one; the client's
    # board-drive tier feeds this, not `netlist` (which is Gowin-mapped).
    assert body["simNetlist"]["modules"]["blink"]


def test_a_null_sim_netlist_still_returns_ok():
    # The generic pass DEGRADES: if it fails, simNetlist is null but the bitstream
    # still comes back. A missing key would be a contract break; null is the
    # honest "no simulatable netlist this time".
    degraded = dict(FAKE_RESULT, sim_netlist=None)
    status, body = _post(_req(), run=lambda *a, **k: degraded)
    assert status == 200 and body["ok"] is True
    assert body["simNetlist"] is None
    assert body["bitstream"] == "QUJD"


def test_a_copyleft_source_is_refused_BEFORE_synthesis_runs():
    # The rule the service exists to enforce. If the flow is reached at all, a
    # GPL source has been compiled on our machine — which is the thing decision
    # 19 forbids, not merely the returning of its output.
    reached = []
    status, body = _post(_req(files=[{"name": "core.v", "source": GPL}]),
                         run=lambda *a, **k: reached.append(1) or FAKE_RESULT)
    assert status == 200
    assert body["ok"] is False
    assert body["code"] == "source-licence-refused"
    assert body["alternative"] == "local-tier"
    assert reached == [], "synthesis must not be reached for a refused source"


def test_an_undeclared_source_still_builds_but_warns():
    status, body = _post(_req(files=[{"name": "mine.v", "source": "module m; endmodule"}]))
    assert body["ok"] is True
    assert [w["code"] for w in body["warnings"]] == ["no-licence-declared"]


def test_a_contract_mismatch_refuses_rather_than_guessing():
    status, body = _post(_req(contract=999))
    assert status == 200 and body["code"] == "contract-mismatch"


def test_a_missing_contract_is_a_mismatch_not_a_default():
    r = _req()
    del r["contract"]
    status, body = _post(r)
    assert body["code"] == "contract-mismatch"


def test_malformed_bodies_are_400_not_500():
    assert _post(b"")[0] == 400
    assert _post(b"{not json")[0] == 400
    assert _post(b'"a string"')[0] == 400
    assert _post(b"x" * (2 * 1024 * 1024 + 1))[0] == 400


def test_no_sources_is_named():
    status, body = _post(_req(files=[]))
    assert body["code"] == "no-sources"


def test_too_many_files_is_bounded():
    many = [{"name": f"f{i}.v", "source": MIT} for i in range(65)]
    status, body = _post(_req(files=many))
    assert body["code"] == "too-many-files"


def test_a_design_error_is_reported_as_itself_with_its_log():
    # NOT an HTTP error: the design did not compile, which is an answer. The
    # client shows the user their own mistake rather than "try again later".
    def boom(*a, **k):
        raise FlowError("synthesis-failed", "yosys failed.", "syntax error at blink.v:3")
    status, body = _post(_req(), run=boom)
    assert status == 200
    assert body["code"] == "synthesis-failed"
    assert "blink.v:3" in body["log"]


def test_a_chipdb_miss_is_NOT_reported_as_a_design_error():
    # The distinction that matters to whoever reads the message: this one is
    # ours, and telling a user to fix their Verilog would waste their time.
    def boom(*a, **k):
        raise FlowError("chipdb-missing", "nextpnr could not load its device database.", "")
    status, body = _post(_req(), run=boom)
    assert body["code"] == "chipdb-missing"


def test_an_unexpected_crash_is_a_500_and_says_so():
    def boom(*a, **k):
        raise RuntimeError("disk on fire")
    status, body = _post(_req(), run=boom)
    assert status == 500 and body["code"] == "internal-error"
