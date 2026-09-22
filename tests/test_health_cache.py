"""tool_versions() memoizes, and a broken result is held only briefly.

Regression for the flag-on staging finding: /api/health re-ran all three WASM
tools (~2.5s) on every call and raced brickwright-lite's 3s backend probe. The
container is immutable, so a healthy result is cached for a long time.

A broken result used not to be cached at all, so that a genuinely down service
kept saying 503 rather than latching a stale answer. That held the wrong end of
the trade: on a host where one tool is permanently unavailable, EVERY request
re-ran the full probe -- 11s per /api/health measured on a serverless host where
yosys could not unpack, against 0.1s from cache on a healthy container. Past
brickwright-lite's 3s cutoff the backend drops out by timeout, which is
indistinguishable from the 503 it was supposed to read. So a failure is cached
now, but for a short TTL, which keeps the original property: recovery is noticed
in seconds rather than after the healthy 600s.

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
    _flow._versions_cache["healthy"] = False


def test_a_healthy_result_is_cached_and_not_recomputed():
    _reset()
    with mock.patch.object(_flow, "_compute_tool_versions", return_value=_HEALTHY) as c:
        assert _flow.tool_versions() == _HEALTHY
        assert _flow.tool_versions() == _HEALTHY
        assert c.call_count == 1, "a healthy result must be served from cache"


def test_a_broken_result_is_cached_too():
    """The 11s-per-call regression: re-probing a known-broken toolchain every time."""
    _reset()
    with mock.patch.object(_flow, "_compute_tool_versions", return_value=_BROKEN) as c:
        assert _flow.tool_versions() == _BROKEN
        assert _flow.tool_versions() == _BROKEN
        assert c.call_count == 1, "a broken result must not be re-probed on every call"


def test_a_broken_result_expires_far_sooner_than_a_healthy_one():
    """The property the old never-cache rule protected: recovery is seen quickly."""
    assert _flow._VERSIONS_TTL_UNHEALTHY_SECONDS < _flow._VERSIONS_TTL_SECONDS
    _reset()
    with mock.patch.object(_flow, "_compute_tool_versions", return_value=_BROKEN) as c:
        _flow.tool_versions()
        # Older than the unhealthy TTL, but far newer than the healthy one: a
        # broken entry must not ride the 600s cache.
        _flow._versions_cache["at"] = time.time() - _flow._VERSIONS_TTL_UNHEALTHY_SECONDS - 1
        _flow.tool_versions()
        assert c.call_count == 2, "a broken result must expire on the short TTL"


def test_a_recovered_toolchain_is_reported_after_the_short_ttl():
    _reset()
    with mock.patch.object(_flow, "_compute_tool_versions", return_value=_BROKEN):
        assert _flow.tool_versions() == _BROKEN
    _flow._versions_cache["at"] = time.time() - _flow._VERSIONS_TTL_UNHEALTHY_SECONDS - 1
    with mock.patch.object(_flow, "_compute_tool_versions", return_value=_HEALTHY):
        assert _flow.tool_versions() == _HEALTHY, "recovery must not be latched out"


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
