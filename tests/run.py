"""Runs the licence tests without pytest, so CI needs no extra dependency."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import tests.test_licence as licence_tests                         # noqa: E402
import tests.test_request_path as request_tests                    # noqa: E402
import tests.test_transport as transport_tests                     # noqa: E402

failed = 0
for module in (licence_tests, request_tests, transport_tests):
    print(f"{module.__name__}:")
    for name in sorted(n for n in dir(module) if n.startswith("test_")):
        try:
            getattr(module, name)()
            print(f"  ok    {name}")
        except AssertionError as e:
            print(f"  FAIL  {name}: {e}")
            failed += 1
        except Exception as e:                                     # noqa: BLE001
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
            failed += 1
print(f"\n{failed} failure(s)")
sys.exit(1 if failed else 0)
