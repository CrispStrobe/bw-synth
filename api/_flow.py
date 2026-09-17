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
import sys
import shutil
import subprocess
import time
import tempfile

# DELIBERATELY SHORTER THAN THE PLATFORM'S LIMIT.
#
# Vercel caps maxDuration at 300s on this plan (the deploy is refused outright
# above it). If a tool were allowed to run to that cap, the PLATFORM would kill
# the function — and the client would get a dead connection or a 504 instead of
# one of this service's named codes. The caller cannot tell "your design is too
# big" from "the service is broken" on a 504.
#
# So the flow gives up first, with `timeout`, a reason, and whatever log the tool
# had produced. Twenty seconds of headroom covers the response being written and
# the temporary directory being cleaned up.
#
# Consequence worth stating: place & route on a LARGE design — a soft CPU, say —
# can legitimately exceed this. Such a design cannot be built on this plan at
# all, and the honest answer is a named timeout rather than a mystery.
TIMEOUT_SECONDS = int(os.environ.get("BW_SYNTH_TIMEOUT", "280"))


# HOW EACH TOOL IS INVOKED, and why it is not simply its name.
#
# Two things bit this in production, in order:
#
#   1. YoWASP names its console script after the PACKAGE, so the Gowin
#      place-and-route command is `yowasp-nextpnr-himbaechel-gowin`, not the
#      `yowasp-nextpnr-himbaechel` you would infer from the binary it wraps.
#   2. On Vercel the dependencies install fine — the build log says so — but
#      their console scripts are NOT on PATH, so every tool reported
#      "No such file or directory" from a live deployment while being present.
#
# So each tool is described by both forms: the script name, and the module to run
# with `python -m`. The first that works is used, and if none does the error
# names every form tried. That is the same move that answered the binary-name and
# chipdb questions in one CI round each — make the failure enumerate what was
# attempted instead of guessing again.
TOOLS = {
    "yosys": {
        "scripts": ("yowasp-yosys",),
        "modules": ("yowasp_yosys",),
        "version": ("-V",),
    },
    "nextpnr": {
        "scripts": ("yowasp-nextpnr-himbaechel-gowin", "yowasp-nextpnr-himbaechel",
                    "nextpnr-himbaechel"),
        "modules": ("yowasp_nextpnr_himbaechel_gowin", "yowasp_nextpnr_himbaechel"),
        "version": ("--version",),
    },
    "gowin_pack": {
        "scripts": ("gowin_pack",),
        "modules": ("apycula.gowin_pack",),
        "version": ("--help",),
    },
}


def _subprocess_env():
    """Environment for every tool subprocess, with sys.path handed down.

    THE BUG THIS FIXES, diagnosed from production rather than guessed. On Vercel
    the dependencies install to /tmp/_vc_deps/lib/python3.12/site-packages and
    the runtime adds that to sys.path AT RUNTIME — not through PYTHONPATH. So the
    function process imports them perfectly well and a freshly spawned
    interpreter cannot see them at all, which is why every tool reported
    "could not be invoked" from a deployment where all three were present and
    importable.

    Handing the parent's sys.path down as PYTHONPATH keeps the subprocess model —
    which is what gives us timeouts, per-step logs and the ability to kill a
    place-and-route that will not finish — without depending on how the platform
    chose to arrange its imports.
    """
    env = dict(os.environ)

    # ONLY /tmp IS WRITABLE IN A LAMBDA. The YoWASP packages unpack their
    # WebAssembly into a cache directory on first run — "Preparing to run
    # yowasp-yosys. This might take a while..." is that step — and the default
    # location is under HOME, which is read-only here. Every one of these points
    # somewhere writable so the unpack can succeed.
    cache = os.environ.get("BW_SYNTH_CACHE", "/tmp/bw-synth-cache")
    os.makedirs(cache, exist_ok=True)
    env.setdefault("YOWASP_CACHE_DIR", cache)
    env["XDG_CACHE_HOME"] = cache
    env["HOME"] = cache
    env["TMPDIR"] = env.get("TMPDIR", "/tmp")

    inherited = [p for p in sys.path if p and os.path.isdir(p)]
    existing = env.get("PYTHONPATH", "")
    if existing:
        inherited.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(inherited)
    return env


def _tool_argv(name):
    """The argv prefix that actually RUNS this tool here.

    Resolution executes the tool's version command and requires exit 0. Checking
    importability was not enough and produced a health probe that lied: the
    YoWASP packages import perfectly and ship no __main__, so `python -m` failed
    while resolution called them present. A probe that reports healthy when the
    work cannot be done defeats the entire point of being fail-closed.

    Forms are tried in order, and failure names every one with what it said.
    """
    spec = TOOLS[name]
    runner = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_runner.py")
    forms = [[s_] for s_ in spec["scripts"]]
    forms += [[sys.executable, "-m", m] for m in spec["modules"]]
    forms += [[sys.executable, runner, s_] for s_ in spec["scripts"]]

    tried = []
    for argv in forms:
        label = " ".join(os.path.basename(a) for a in argv)
        if len(argv) == 1 and not shutil.which(argv[0]):
            tried.append(f"{label} (not on PATH)")
            continue
        try:
            probe = subprocess.run(argv + list(spec["version"]),
                                   capture_output=True, text=True, timeout=120,
                                   env=_subprocess_env())
        except Exception as e:                  # noqa: BLE001
            tried.append(f"{label} ({type(e).__name__})")
            continue
        if probe.returncode == 0:
            return argv
        # Keep the LAST line, not the first, and keep more of it: YoWASP prints
        # a progress banner before it fails, so the first line is never the error.
        lines = [ln for ln in (probe.stderr or probe.stdout or "").strip().splitlines() if ln.strip()]
        detail = (lines[-1] if lines else "")[:220]
        tried.append(f"{label} (exit {probe.returncode}: {detail})")

    raise FlowError("tool-missing",
                    f"{name} could not be invoked. Tried: " + "; ".join(tried))


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
                              timeout=TIMEOUT_SECONDS, env=_subprocess_env())
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


def sim_netlist(work, sources, top):
    """A TECHNOLOGY-INDEPENDENT netlist for the gate-level simulator.

    -> (netlist_dict, log_str); raises FlowError if yosys fails.

    `synth_gowin` (the pass that feeds the bitstream) emits a GOWIN-MAPPED
    netlist: LUTs, OBUFs, DFFs and the primitive library's `$specify2` timing
    cells. That is exactly what nextpnr and gowin_pack need, and exactly what
    `yosys2digitaljs` — the front end of brickwright-lite's gate-level tier —
    CANNOT read: fed the mapped netlist it dies with `Invalid cell type:
    $specify2`, so a synthesised design could never light the on-screen board.
    (Found by feeding the first real synth_gowin netlist to the sim; see
    brickwright-lite docs/TANG-NANO.md §7.6.)

    The simulator wants GENERIC cells ($and/$mux/$dff/$add …), so this runs the
    same coarse flow `yosys2digitaljs` runs itself — proc, opt, memory, NO
    techmap, NO abc — from clean sources, because a gowin-mapped netlist cannot be
    un-mapped back to generic cells. The `top` attribute Yosys writes is a binary
    string (`"00…01"`), which the client's reader decodes.
    """
    read = " ".join(f"read_verilog {s};" for s in sources)
    hier = (f"hierarchy -top {top}" if top
            else "setattr -mod -unset top; hierarchy -auto-top")
    script = (f"{read} {hier}; proc; opt; memory -nomap; wreduce -memx; "
              "opt -full; write_json design_sim.json")
    out = _run(_tool_argv("yosys") + ["-p", script], work, "yosys (sim netlist)")
    with open(os.path.join(work, "design_sim.json"), encoding="utf-8") as fh:
        return json.load(fh), out


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
            _tool_argv("yosys")
            + ["-p", f"{read} synth_gowin{top_arg} -json design.json"],
            work, "yosys"))

        packed = os.path.join(work, "design_pnr.json")
        log.append(_run(
            _tool_argv("nextpnr") + ["--device", device,
             "--vopt", f"family={family}", "--vopt", f"cst=design.cst",
             "--json", "design.json", "--write", "design_pnr.json"],
            work, "nextpnr"))

        bitstream = None
        try:
            # gowin_pack wants the same family/chip name, not the full ordering
            # code: the part number is what you buy, the family is what the
            # bitstream format belongs to.
            log.append(_run(_tool_argv("gowin_pack")
                            + ["-d", family, "-o", "design.fs", "design_pnr.json"],
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

        # A SECOND, technology-independent netlist — for the gate-level simulator,
        # not the board. See sim_netlist() for why the mapped netlist above cannot
        # be simulated. It DEGRADES rather than fails: the bitstream is the primary
        # product, so a design that mapped for the board but tripped the generic
        # pass still returns its bitstream, and the board-drive tier no-ops without
        # a netlist.
        sim = None
        try:
            sim, sim_log = sim_netlist(work, sources, top)
            log.append(sim_log)
        except (FlowError, OSError, ValueError) as e:
            reason = getattr(e, "reason", str(e))
            log.append(f"sim-netlist pass failed, returning simNetlist as null: {reason}")

        return {"netlist": netlist, "sim_netlist": sim,
                "bitstream_b64": bitstream,
                "log": "\n".join(log), "toolVersions": tool_versions()}
    finally:
        shutil.rmtree(work, ignore_errors=True)


# tool_versions() runs all three WASM tools to PROVE execution, which costs
# ~2.5s. /api/health calls it, and brickwright-lite's fail-closed backend probe
# calls /api/health on every page load with a 3s timeout — so an honest 2.5s
# health check races that timeout and the hosted backend flickers in and out of
# "available". Found by the flag-on staging build (fpga.crispstro.be), which is
# exactly what a staging build is for.
#
# The container is immutable: read-only rootfs, wasm baked at build, tools that
# cannot change within its life. So a HEALTHY result is cached — once the tools
# are proven to run, they keep running. An UNHEALTHY result is NOT cached, so a
# service that is genuinely broken keeps saying so on every probe rather than
# latching a stale 503. The first call still pays the full cost and still proves
# execution; app.py warms it at import so even the first probe is fast.
_VERSIONS_TTL_SECONDS = 600
_versions_cache = {"at": 0.0, "value": None}


def tool_versions(*, use_cache=True):
    """Reported with every build, and by /api/health.

    A bitstream is only reproducible against known tools, and the health probe is
    fail-closed: a backend that cannot do the work must say so rather than accept
    a request it will fail. Resolution proves each tool RUNS, so an entry here is
    either a real version string or a reason, never a hopeful guess.

    A healthy result is memoized (see above); pass use_cache=False to force a
    fresh probe.
    """
    if use_cache and _versions_cache["value"] is not None \
            and (time.time() - _versions_cache["at"]) < _VERSIONS_TTL_SECONDS:
        return _versions_cache["value"]
    out = _compute_tool_versions()
    # Cache only when every tool resolved. `unavailable:` is the one shape a
    # broken tool produces, so its absence means all three ran.
    if not any(str(v).startswith("unavailable:") for v in out.values()):
        _versions_cache["value"] = out
        _versions_cache["at"] = time.time()
    return out


def _compute_tool_versions():
    out = {}
    for name, spec in TOOLS.items():
        try:
            argv = _tool_argv(name)
        except FlowError as e:
            out[name] = f"unavailable: {e.reason}"
            continue
        try:
            p = subprocess.run(argv + list(spec["version"]), capture_output=True,
                               text=True, timeout=120, env=_subprocess_env())
            text = (p.stdout or p.stderr or "").strip()
            out[name] = text.splitlines()[0][:120] if text else "(ran, no version output)"
        except Exception as e:                  # noqa: BLE001
            out[name] = f"unavailable: {e}"
    return out
