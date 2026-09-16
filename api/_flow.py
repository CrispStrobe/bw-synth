"""Running the Gowin open flow: Verilog -> netlist -> place & route -> bitstream.

Three tools, all permissive, which is why this service can exist:

    yosys                  ISC   synthesis; also emits the JSON netlist the
                                 gate-level simulator in brickwright-lite eats
    nextpnr-himbaechel     ISC   place & route for Gowin
    apycula (gowin_pack)   MIT   the bitstream packer

THE FLAG THAT COSTS AN AFTERNOON IF MISSING: C-grade devices — and the Tang Nano
20K's GW2AR-LV18QN88C8/I7 is one — require `--vopt family=<FAMILY>` on nextpnr
and the matching `-d <DEVICE>` on gowin_pack. Omitting it fails in a way that
reads like a broken design rather than a missing switch, which is exactly the
kind of error this service should never hand back.

Everything runs in a temporary directory that is removed afterwards. Nothing is
persisted: the inputs are somebody else's source code.
"""
import json
import os
import shutil
import subprocess
import tempfile

TIMEOUT_SECONDS = int(os.environ.get("BW_SYNTH_TIMEOUT", "600"))


# YoWASP names its console script after the package, and the package name is not
# always the tool name: `yowasp-nextpnr-himbaechel-gowin` ships a command of the
# same name, not the `yowasp-nextpnr-himbaechel` you would guess from the binary
# it wraps. The first CI run failed exactly there — yosys and gowin_pack ran, and
# nextpnr was "No such file or directory".
#
# So the binary is DISCOVERED rather than assumed, and a failure names every
# candidate tried. A hard-coded guess fails opaquely on the next rename; this
# fails with the list of things it looked for.
NEXTPNR_CANDIDATES = (
    "yowasp-nextpnr-himbaechel-gowin",
    "yowasp-nextpnr-himbaechel",
    "nextpnr-himbaechel",
)


def _resolve(candidates, what):
    for name in candidates:
        if shutil.which(name):
            return name
    raise FlowError(
        "tool-missing",
        f"{what} is not installed. Tried: {', '.join(candidates)}.")


class FlowError(Exception):
    def __init__(self, code, reason, log=""):
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.log = log


def _available_chipdbs():
    """Which device databases this nextpnr build actually ships.

    Asked only when nextpnr says it cannot find one. The error it gives —
    "Unable to read chipdb /share/himbaechel/gowin/chipdb-GW2A.bin" — names the
    file it wanted and not one of the files it has, which leaves you guessing at
    a string. Listing them turns the next failure into an answer.
    """
    import glob
    import sysconfig
    roots = [
        os.path.join(sysconfig.get_paths().get("purelib", ""), "yowasp_nextpnr_himbaechel_gowin"),
        os.path.join(sysconfig.get_paths().get("platlib", ""), "yowasp_nextpnr_himbaechel_gowin"),
        "/share/himbaechel",
    ]
    found = []
    for root in roots:
        if not root:
            continue
        found += glob.glob(os.path.join(root, "**", "chipdb*"), recursive=True)
    return sorted({os.path.basename(f) for f in found}) or ["(none found on disk)"]


def _run(argv, cwd, step):
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                              timeout=TIMEOUT_SECONDS)
    except FileNotFoundError as e:
        raise FlowError("tool-missing", f"{step}: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise FlowError("timeout",
                        f"{step} exceeded {TIMEOUT_SECONDS}s. Place and route on a large "
                        "design can legitimately take minutes; a combinational loop can "
                        "take forever.", (e.stdout or "") + (e.stderr or "")) from e
    log = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        # A chipdb miss is NOT a design error and must not be reported as one —
        # the user's Verilog is fine and no amount of editing it will help. It is
        # a packaging or device-name problem on this side, so it gets its own code
        # and carries the list of databases that DO exist.
        if "chipdb" in log:
            raise FlowError(
                "chipdb-missing",
                f"{step} could not load its device database. This is a configuration "
                f"problem here, not a problem with the design. Available: "
                f"{', '.join(_available_chipdbs())}",
                log)
        # A DESIGN error. Reported as itself so the client shows the user their
        # own mistake rather than "try again".
        raise FlowError("synthesis-failed", f"{step} failed.", log)
    return log


def synthesise(files, constraints, top, target):
    """-> {"netlist": {...}, "bitstream_b64": str|None, "log": str, "toolVersions": {...}}"""
    work = tempfile.mkdtemp(prefix="bw-synth-")
    log = []
    try:
        sources = []
        for f in files:
            name = os.path.basename(f["name"]) or "design.v"
            path = os.path.join(work, name)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(f.get("source", ""))
            sources.append(name)

        cst = os.path.join(work, "design.cst")
        with open(cst, "w", encoding="utf-8") as fh:
            fh.write(constraints or "")

        # The FAMILY is a chipdb name, not a marketing name, and the two differ.
        # nextpnr ships: GW1N-1, GW1N-4, GW1N-9, GW1N-9C, GW1NS-4, GW1NZ-1,
        # GW2A-18, GW2A-18C, GW5A-25A, GW5AST-138C (reported by CI, 2026-09-16).
        #
        # The Tang Nano 20K's GW2AR-LV18QN88C8/I7 is a C-GRADE GW2A-18 — that is
        # what the "C8" means — so its database is GW2A-18C and not the "GW2A" a
        # reader infers from the part number. Apicula's readme saying "C devices
        # require passing --vopt family" is about exactly this distinction.
        family = target.get("family", "GW2A-18C")
        device = target.get("device", "GW2AR-LV18QN88C8/I7")
        json_netlist = os.path.join(work, "design.json")

        read = " ".join(f"read_verilog {s};" for s in sources)
        top_arg = f" -top {top}" if top else ""
        log.append(_run(
            ["yowasp-yosys", "-p", f"{read} synth_gowin{top_arg} -json design.json"],
            work, "yosys"))

        packed = os.path.join(work, "design_pnr.json")
        nextpnr = _resolve(NEXTPNR_CANDIDATES, "nextpnr")
        log.append(_run(
            [nextpnr, "--device", device,
             "--vopt", f"family={family}", "--vopt", f"cst=design.cst",
             "--json", "design.json", "--write", "design_pnr.json"],
            work, "nextpnr"))

        bitstream = None
        try:
            # gowin_pack wants the same family/chip name, not the full ordering
            # code: the part number is what you buy, the family is what the
            # bitstream format belongs to.
            log.append(_run(["gowin_pack", "-d", family, "-o", "design.fs", "design_pnr.json"],
                            work, "gowin_pack"))
            with open(os.path.join(work, "design.fs"), "rb") as fh:
                import base64
                bitstream = base64.b64encode(fh.read()).decode("ascii")
        except FlowError as e:
            # A netlist without a bitstream is still useful — it drives the
            # gate-level tier — so this degrades rather than failing the request,
            # and says so instead of pretending the bitstream is absent by design.
            log.append(f"gowin_pack failed, returning the netlist only: {e.reason}\n{e.log}")

        with open(json_netlist, encoding="utf-8") as fh:
            netlist = json.load(fh)

        return {"netlist": netlist, "bitstream_b64": bitstream,
                "log": "\n".join(log), "toolVersions": tool_versions()}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def tool_versions():
    """Reported with every build: a bitstream is only reproducible against known tools."""
    out = {}
    try:
        nextpnr = _resolve(NEXTPNR_CANDIDATES, "nextpnr")
    except FlowError as e:
        nextpnr = None
        out["nextpnr"] = f"unavailable: {e.reason}"
    for name, argv in (("yosys", ["yowasp-yosys", "-V"]),
                       ("nextpnr", [nextpnr, "--version"] if nextpnr else None),
                       ("apycula", ["gowin_pack", "--help"])):
        if argv is None:
            continue
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=30)
            out[name] = (p.stdout or p.stderr or "").strip().splitlines()[0][:120]
        except Exception as e:      # noqa: BLE001 — a missing tool must not 500 the probe
            out[name] = f"unavailable: {e}"
    return out
