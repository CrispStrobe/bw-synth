"""tool_versions() memoizes a healthy result but never a broken one.

Regression for the flag-on staging finding: /api/health re-ran all three WASM
tools (~2.5s) on every call and raced brickwright-lite's 3s backend probe. The
container is immutable, so a healthy result is cached; a broken one is not, so a
genuinely down service keeps saying 503 rather than latching a stale answer.

Plain test_* functions, matching tests/run.py's convention (no pytest in CI).
"""
import pathlib
import sys
import time
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from api import _flow                                              # noqa: E402

_HEALTHY = {"yosys": "Yosys 0.69", "nextpnr": "nextpnr-0.11", "gowin_pack": "usage: gowin_pack"}
_BROKEN = {"yosys": "Yosys 0.69", "nextpnr": "unavailable: could not be invoked",
           "gowin_pack": "usage: gowin_pack"}


def _reset():
    _flow._versions_cache["value"] = None
    _flow._versions_cache["at"] = 0.0


def test_a_healthy_result_is_cached_and_not_recomputed():
    _reset()
    with mock.patch.object(_flow, "_compute_tool_versions", return_value=_HEALTHY) as c:
        assert _flow.tool_versions() == _HEALTHY
        assert _flow.tool_versions() == _HEALTHY
        assert c.call_count == 1, "a healthy result must be served from cache"


def test_a_broken_result_is_never_cached():
    _reset()
    with mock.patch.object(_flow, "_compute_tool_versions", return_value=_BROKEN) as c:
        assert _flow.tool_versions() == _BROKEN
        assert _flow.tool_versions() == _BROKEN
        assert c.call_count == 2, "a broken result must be re-probed, not latched into a stale 503"


def test_an_expired_cache_recomputes():
    _reset()
    with mock.patch.object(_flow, "_compute_tool_versions", return_value=_HEALTHY) as c:
        _flow.tool_versions()
        _flow._versions_cache["at"] = time.time() - _flow._VERSIONS_TTL_SECONDS - 1
        _flow.tool_versions()
        assert c.call_count == 2, "an expired cache must recompute"


def test_use_cache_false_forces_a_fresh_probe():
    _reset()
    with mock.patch.object(_flow, "_compute_tool_versions", return_value=_HEALTHY) as c:
        _flow.tool_versions()
        _flow.tool_versions(use_cache=False)
        assert c.call_count == 2
