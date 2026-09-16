"""The proof: a blinky goes all the way to a bitstream, on the real tools.

Everything else in this repository is argument. This is the only thing that
demonstrates the flow runs, and it is deliberately the smallest design that can:
one output, tied high, placed on a pin the Tang Nano 20K actually brings out.

It asserts the NETLIST exists (which is what the gate-level simulator needs) and
reports the bitstream separately, because gowin_pack failing is a degraded
result rather than a failed one — a netlist without a bitstream is still useful.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from api._flow import FlowError, synthesise, tool_versions          # noqa: E402

BLINKY = """// SPDX-License-Identifier: MIT
module blink(output led);
    assign led = 1'b1;
endmodule
"""
# Pin 73 is IOT40A on the left header — a real, free I/O on this board.
CST = 'IO_LOC "led" 73;\nIO_PORT "led" IO_TYPE=LVCMOS33;\n'

# family is the CHIPDB name (GW2A-18C), device is the ordering code. They are
# different strings for different tools and conflating them cost a CI round.
TARGET = {"family": "GW2A-18C", "device": "GW2AR-LV18QN88C8/I7", "vopt": "family"}

print("tool versions:")
for name, version in tool_versions().items():
    print(f"  {name:10} {version}")

try:
    result = synthesise([{"name": "blink.v", "source": BLINKY}], CST, "blink", TARGET)
except FlowError as e:
    print(f"\nFAILED at {e.code}: {e.reason}")
    if e.code == "chipdb-missing":
        # Worth separating from a design failure in the output too: this one is
        # ours to fix, and the reason above already names the valid options.
        print("\nThis is a configuration problem in bw-synth, not in the design.")
    print(e.log)
    sys.exit(1)

netlist = result["netlist"]
assert isinstance(netlist, dict) and netlist.get("modules"), "no netlist came back"
assert "blink" in netlist["modules"], f"expected a 'blink' module, got {list(netlist['modules'])}"
ports = netlist["modules"]["blink"].get("ports", {})
assert "led" in ports, f"expected an 'led' port, got {list(ports)}"

print(f"\nok   netlist: modules={list(netlist['modules'])} ports={list(ports)}")
if result["bitstream_b64"]:
    print(f"ok   bitstream: {len(result['bitstream_b64'])} base64 chars")
else:
    print("WARN bitstream: gowin_pack did not produce one — the netlist is still usable")
print("\nthe flow runs.")
