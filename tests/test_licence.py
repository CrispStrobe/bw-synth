"""The server-side licence screen — the half that cannot be bypassed.

Mirrors brickwright-lite's test/fpga-synthesis-contract.test.mjs on purpose: the
same rules, asserted independently, so the two screens cannot drift into
disagreeing about what may be built where.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from api._licence import detect_licence, screen   # noqa: E402

def test_spdx_permissive_and_copyleft():
    assert detect_licence("// SPDX-License-Identifier: MIT")[1] == "permissive"
    assert detect_licence("// SPDX-License-Identifier: GPL-3.0-or-later")[1] == "copyleft"
    assert detect_licence("/* SPDX-License-Identifier: Apache-2.0 */")[1] == "permissive"
    assert detect_licence("// SPDX-License-Identifier: LGPL-2.1-only")[1] == "copyleft"

def test_dual_offer_is_the_recipients_choice():
    # The same reasoning that settles EPL-2.0 OR GPL upstream: we elect, nothing
    # propagates. Reading only the first half would refuse something legitimate.
    assert detect_licence("// SPDX-License-Identifier: GPL-2.0-or-later OR MIT")[1] == "permissive"

def test_block_comment_terminator_is_not_part_of_the_id():
    assert detect_licence("/* SPDX-License-Identifier: MIT */")[0] == "MIT"

def test_prose_gnu_notice_without_a_tag():
    spdx, family, evidence = detect_licence(
        "// This program is free software under the terms of the GNU General Public License")
    assert family == "copyleft"
    assert "GNU" in evidence

def test_no_declaration_is_unknown_never_permissive():
    # The distinction the whole module exists to preserve.
    spdx, family, _ = detect_licence("module blink(output led); endmodule")
    assert family == "unknown"
    assert spdx is None

def test_copyleft_is_refused_and_points_at_the_local_route():
    refusals, warnings = screen([{"name": "core.v",
                                  "source": "// SPDX-License-Identifier: GPL-3.0-only"}])
    assert [r["code"] for r in refusals] == ["copyleft-source"]
    assert "locally" in refusals[0]["reason"]

def test_undeclared_warns_but_is_not_refused():
    refusals, warnings = screen([{"name": "mine.v", "source": "module m; endmodule"}])
    assert refusals == []
    assert [w["code"] for w in warnings] == ["no-licence-declared"]
