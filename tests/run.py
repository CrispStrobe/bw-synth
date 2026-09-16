"""Runs the licence tests without pytest, so CI needs no extra dependency."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import tests.test_licence as t                                     # noqa: E402

failed = 0
for name in sorted(n for n in dir(t) if n.startswith("test_")):
    try:
        getattr(t, name)()
        print(f"  ok    {name}")
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        failed += 1
print(f"\n{failed} failure(s)")
sys.exit(1 if failed else 0)
