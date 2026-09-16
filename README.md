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

## Deployment: it runs, and the toolchain does not fit

**Deployed at https://bw-synth.vercel.app — and synthesis cannot run there.**
Measured from inside the live function, not inferred:

    /tmp         525 MB total,   0 MB free
    dependencies             333 MB   (installed into /tmp by the runtime)
    /var/task     31 MB total,   0 MB free

The three tools install to `/tmp/_vc_deps` and the YoWASP packages then unpack
their WebAssembly into the same filesystem on first run — that is the "Preparing
to run yowasp-yosys. This might take a while..." step. There is nothing left for
it, and the tool dies with `OSError: [Errno 28] No space left on device`.

**It has a second face, and they are the same fault.** Depending on how far the
process gets, `/api/health` reports instead:

    FileNotFoundError: [Errno 2] No usable temporary directory found in
    ['/tmp', '/tmp', '/var/tmp', '/usr/tmp', '/var/task']

That is Python's `tempfile` rejecting every candidate because none of them is
writable with room to spare — not a missing directory. Both strings mean 0 MB
free, and both are recorded here so whoever meets one recognises the other.

**This is not a plan limit that money fixes, or not obviously.** The package-size
ceiling was never the problem — the deployment builds fine. The constraint is
**writable ephemeral storage at runtime**, and a ~330 MB toolchain that unpacks
another ~100 MB does not fit in 525 MB.

`/api/health` says so, in those words, with the numbers. The service refuses
work it cannot do rather than accepting a request and failing mid-synthesis,
which is the whole reason that endpoint is fail-closed.

### What does work, today, in production

- the app, routing, and the contract
- **the licence rule**: POST a GPL-3.0 source to `/api/synth` and it is refused
  by name, with evidence, pointing at the local tier — and synthesis is never
  reached. That is the rule this service exists to enforce, and it is enforced.
- `gowin_pack` resolves and runs; only the two WebAssembly tools cannot unpack.

### Where it should probably live instead

A **container** — Fly, Railway, Render, or anything with a real disk — where
330 MB is unremarkable. The flow is proven (see below); it needs a filesystem,
not a rewrite. Vercel Sandbox is the other candidate, being designed for exactly
this shape of work.

Keeping it on a Vercel function would mean fitting the toolchain into ~190 MB of
free ephemeral space, which no amount of configuration here achieves.

## Status

**The flow works. The service has never served a request. It is not deployed.**

Those are three different claims and they are worth keeping apart.

| | evidence |
|---|---|
| **Synthesis runs end to end** | `tests/smoke_blinky.py` in CI: Verilog → netlist → place & route → a **6.16 MB bitstream**, on yosys 0.69 + nextpnr-himbaechel-gowin + apycula |
| **The request path is correct** | `tests/test_request_path.py`, 11 cases — contract, licence refusal, body limits, which failures are HTTP errors rather than answers |
| **The licence screen refuses what it must** | `tests/test_licence.py`, 7 cases |
| **HTTP transport** | `tests/test_transport.py`, 6 cases — the handler driven with fake streams |
| **Vercel's runtime invoking it** | *unverified* — only a deploy answers this |
| **Deployment** | *not done* |

The first deploy plus one request from brickwright-lite's client is what closes
the remaining gap, and it is now a narrow one: everything beneath the socket is
covered, and what is left is whether Vercel's Python runtime invokes a
`BaseHTTPRequestHandler` subclass the way the class expects. No test here can
answer that.

### What getting here cost, in case it saves someone the same rounds

Four CI failures, and the first three were the same mistake wearing different
clothes — guessing a plausible string against a toolchain nobody had run:

1. `apycula==0.33` — but `yowasp-nextpnr-himbaechel-gowin` pins `Apycula==0.32`
   exactly, because place & route and the packer must agree on the device
   database. **That version is not ours to choose.**
2. `yowasp-nextpnr-himbaechel` — the console script is named after the *package*,
   so it is `yowasp-nextpnr-himbaechel-gowin`.
3. `--vopt family=GW2A` — there is no `GW2A` chipdb.
4. Fixed by asking: the flow now lists the databases it *has* when it cannot find
   the one asked for, and CI answered in one round — **`GW2A-18C`**.

`family` and `device` are different strings for different tools:

    device  GW2AR-LV18QN88C8/I7   the ordering code — what you buy, --device
    family  GW2A-18C              the chipdb — what nextpnr loads, gowin_pack -d

The Tang Nano 20K's part is a C-grade GW2A-18; that is what the `C8` means. This
is also what apicula's "C devices require `--vopt family`" is distinguishing —
the C-grade database from the plain one.

Nothing is persisted: inputs are somebody else's source code, and each request
works in a temporary directory that is removed afterwards.
