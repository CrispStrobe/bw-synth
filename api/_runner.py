"""Run a packaged console script in-process, by its declared entry point.

WHY THIS EXISTS. Three invocation forms have now failed on Vercel, each for its
own reason:

  the script name          installed, but not on PATH in the Lambda runtime
  python -m <package>      the YoWASP packages ship no __main__
  a guessed callable       the road that cost three CI rounds on this project

So this asks the PACKAGING METADATA instead. `importlib.metadata` records the
exact callable behind every console script — `yowasp-yosys = yowasp_yosys:_run`
or whatever it happens to be — and that is authoritative rather than inferred.
If the entry point is missing, the error lists the console scripts that DO exist,
which is the same "make the failure enumerate" move that answered the binary-name
and chipdb questions in one round each.

Invoked as a subprocess so the caller keeps timeouts, per-step logs and the
ability to kill a place-and-route that will not finish.

    python _runner.py <console-script-name> [args...]
"""
import sys


def main(argv):
    if len(argv) < 2:
        print("usage: _runner.py <console-script-name> [args...]", file=sys.stderr)
        return 2

    wanted = argv[1]
    args = argv[2:]

    from importlib.metadata import entry_points
    scripts = entry_points(group="console_scripts")
    match = next((e for e in scripts if e.name == wanted), None)
    if match is None:
        available = sorted({e.name for e in scripts})
        print(f"no console script named {wanted!r}. Available: {', '.join(available) or '(none)'}",
              file=sys.stderr)
        return 127

    func = match.load()
    sys.argv = [wanted] + args
    try:
        result = func()
    except SystemExit as e:                      # console scripts exit rather than return
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
