# bw-synth — Gowin FPGA synthesis as a service

Verilog and constraints in; a Yosys JSON netlist and a `.fs` bitstream out.
Serves the TN3 contract defined by
[brickwright-lite](https://github.com/CrispStrobe/brickwright-lite)
(`docs/TANG-NANO.md`, `overlay/scratch-gui/src/lib/bw-fpga/synthesis.js`).

**It is deliberately its own service.** brickwright-lite's Vercel project builds
the editor; bolting a ~300 MB toolchain into it would couple the editor's deploy
to the toolchain's and inflate a build that already ratchets on payload. This is
the same arrangement `stc-compiler.vercel.app` already has for the GPL MCU
toolchains — separate repo, separate deploy, called by URL.

## Why this can exist at all

The entire open Gowin flow is permissively licensed, which is unusual and is the
whole reason a permissive product can offer it:

| tool | licence | job |
|---|---|---|
| [Yosys](https://github.com/YosysHQ/yosys) | ISC | synthesis, and the JSON netlist the gate-level simulator consumes |
| [nextpnr](https://github.com/YosysHQ/nextpnr) (himbaechel/Gowin) | ISC | place & route |
| [Project Apicula](https://github.com/YosysHQ/apicula) (`gowin_pack`) | MIT | bitstream packing |

It runs on **Python** rather than Node because `gowin_pack` is Python; all three
are on PyPI, so one runtime installs the whole flow.

## What it refuses, and why that is the point

**Copyleft sources are refused.** Compiling GPL source here and returning a
bitstream would convey a derivative work and make this service a distributor of
that licence. Building the same design *locally*, in the user's own browser,
conveys nothing to anyone — so that is where those designs go.

brickwright-lite screens before uploading, which is right for the user
experience: a refusal after the source has left the machine has already lost.
**This screens again anyway**, because that is a client and a client can be
bypassed, and a policy that depends on a browser is not a policy.

The screen can refuse on positive evidence (an SPDX tag, a GNU notice). It can
**never approve**: absence of a notice is not evidence of a permissive licence,
so an undeclared source is reported as `unknown` and warned about, not blessed.

## The contract (v1)

```
POST /api/synth
{ "contract": 1,
  "target": {"family": "GW2A", "device": "GW2AR-LV18QN88C8/I7", "vopt": "family"},
  "top": "blink",
  "files": [{"name": "blink.v", "source": "..."}],
  "constraints": "IO_LOC \"led\" 73;\n..." }

200 { "contract": 1, "ok": true, "netlist": {...}, "bitstream": "<base64>|null",
      "log": "...", "toolVersions": {...} }
200 { "contract": 1, "ok": false, "code": "...", "reason": "...", "log": "..." }

GET /api/health   ->  200 when the toolchain is genuinely present, 503 otherwise
```

Every failure is a **200 with a named code**, except a malformed request (400)
and an unexpected crash (500). *A design that does not compile is not an HTTP
error* — it is an answer, and the client shows the user their own mistake rather
than "try again later".

`/api/health` exists because the client's backend selector is fail-closed: a
backend that does not answer is not offered at all. An unhealthy service must say
so rather than accept work it cannot do.

## The flag that costs an afternoon

C-grade Gowin devices — and the Tang Nano 20K's `GW2AR-LV18QN88C8/I7` is one —
need `--vopt family=<FAMILY>` on nextpnr and the matching `-d <DEVICE>` on
`gowin_pack`. Omitting it fails in a way that reads like a broken design rather
than a missing switch.

## Status

**Not yet deployed, and not yet run against the real toolchain.** The handlers,
the contract and the licence screen are written and the screen is tested; the
flow has not executed a synthesis on this machine, because the toolchain is not
installed here. Treat the first real build as the thing that proves it.

Nothing is persisted: inputs are somebody else's source code, and each request
works in a temporary directory that is removed afterwards.
