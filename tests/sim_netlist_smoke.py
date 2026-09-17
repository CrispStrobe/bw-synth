"""The generic sim netlist, on the design that most needs it: a counter.

blink (in smoke_blinky) proves a combinational design reduces to a generic
netlist. This proves a SEQUENTIAL one does — the case #2 (the clock) exists for.
It runs only yosys (not nextpnr/gowin_pack), so it is fast and needs just that
one tool, and it asserts the shape the simulator depends on: the design alone,
no Gowin primitives, no $specify2, and a real flip-flop the clock can step.
"""
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from api._flow import FlowError, sim_netlist                        # noqa: E402

COUNTER = """// SPDX-License-Identifier: MIT
module sequence(input clk, input rst_n, output [3:0] led);
  reg [3:0] cnt;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) cnt <= 4'd0;
    else        cnt <= cnt + 1'b1;
  assign led = cnt;
endmodule
"""

work = tempfile.mkdtemp(prefix="bw-simnetlist-")
try:
    with open(os.path.join(work, "sequence.v"), "w", encoding="utf-8") as fh:
        fh.write(COUNTER)
    try:
        netlist, log = sim_netlist(work, ["sequence.v"], "sequence")
    except FlowError as e:
        print(f"FAILED at {e.code}: {e.reason}\n{e.log}")
        sys.exit(1)

    mods = netlist.get("modules", {})
    assert "sequence" in mods, f"no 'sequence' module: {list(mods)}"
    assert len(mods) == 1, f"a generic netlist is the design alone, got {len(mods)} modules"
    top = mods["sequence"]
    ports = top.get("ports", {})
    for p in ("clk", "rst_n", "led"):
        assert p in ports, f"lost port {p}: {list(ports)}"

    types = sorted({c.get("type") for c in (top.get("cells") or {}).values()})
    # Every cell must be a GENERIC ($-prefixed) yosys cell; a Gowin primitive
    # (OBUF/LUT/DFF…) or a $specify2 timing cell would make yosys2digitaljs refuse.
    non_generic = [t for t in types if t == "$specify2" or (t and not t.startswith("$"))]
    assert not non_generic, f"non-generic cells the simulator rejects: {non_generic}"
    # A counter must keep a flip-flop for the clock to step; $add for +1.
    assert any("dff" in t for t in types), f"no flip-flop in a clocked design: {types}"
    assert "$add" in types, f"no adder for the +1: {types}"

    # The top attribute is a Yosys binary string; the client decodes it.
    assert str(top.get("attributes", {}).get("top", "")).lstrip("0") in ("", "1"), \
        "top attribute is not the binary form the client expects"

    print(f"ok   sequence sim netlist: 1 module, generic cells {types}")
    print("the generic sim-netlist pass runs, and a clocked design keeps its flop.")
finally:
    import shutil
    shutil.rmtree(work, ignore_errors=True)
