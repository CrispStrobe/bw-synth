"""Reading a licence declaration out of HDL source — the server-side half.

brickwright-lite screens sources BEFORE upload (lib/bw-fpga/licence.js), which is
the right place for the user experience: a refusal that happens after the source
has left the machine has already lost.

This is the same rule enforced again here, and the duplication is deliberate.
That client is a client. It can be bypassed by anyone who posts to this endpoint
directly, and the rule it enforces is not a UX nicety — it is the reason this
service is not a GPL distributor. A policy that depends on a browser is not a
policy.

WHAT THIS CAN AND CANNOT DO, the same as the client:

  It can REFUSE on positive evidence — an SPDX tag or a GNU notice in the text.
  It can NEVER APPROVE. Absence of a notice is not evidence of a permissive
  licence, and most HDL carries no declaration at all.

So `unknown` is reported, not treated as consent, and the caller decides.
"""
import re

PERMISSIVE = re.compile(
    r"^(MIT|ISC|BSD-2-Clause|BSD-3-Clause|Apache-2\.0|MPL-2\.0|0BSD|Unlicense|CC0-1\.0)$",
    re.I)
COPYLEFT = re.compile(
    r"^(A?GPL-[0-9.]+(-only|-or-later)?|LGPL-[0-9.]+(-only|-or-later)?|GPL-[0-9.]+\+?)$",
    re.I)
# The whole expression, not the first token: "GPL-2.0-or-later OR MIT" is a dual
# offer the recipient may elect MIT from, and stopping at whitespace reads it as
# its first half and refuses something legitimate.
SPDX_TAG = re.compile(r"SPDX-License-Identifier:[ \t]*([^\n\r]+)", re.I)
PROSE = [
    (re.compile(r"GNU\s+(Lesser\s+|Affero\s+)?General\s+Public\s+License", re.I),
     "GPL-family (prose notice)"),
    (re.compile(r"under\s+the\s+terms\s+of\s+the\s+GNU", re.I),
     "GPL-family (prose notice)"),
]


def _trim(expr: str) -> str:
    expr = re.sub(r"\*/.*$", "", expr)
    expr = re.sub(r"//.*$", "", expr)
    return expr.strip(" \t;,.")


def detect_licence(source: str):
    """-> (spdx | None, family in {'permissive','copyleft','unknown'}, evidence | None)"""
    text = source or ""
    tag = SPDX_TAG.search(text)
    if tag:
        spdx = _trim(tag.group(1))
        halves = [h.strip() for h in re.split(r"\s+OR\s+", spdx, flags=re.I)]
        if any(PERMISSIVE.match(h) for h in halves):
            return spdx, "permissive", f"SPDX-License-Identifier: {spdx}"
        if halves and all(COPYLEFT.match(h) for h in halves):
            return spdx, "copyleft", f"SPDX-License-Identifier: {spdx}"
        return spdx, "unknown", f"SPDX-License-Identifier: {spdx}"

    for pattern, spdx in PROSE:
        m = pattern.search(text)
        if m:
            return spdx, "copyleft", m.group(0).strip()

    return None, "unknown", None


def screen(files):
    """-> (refusals, warnings). A copyleft source is refused; an undeclared one warns."""
    refusals, warnings = [], []
    for f in files or []:
        name = f.get("name", "<unnamed>")
        spdx, family, evidence = detect_licence(f.get("source", ""))
        if family == "copyleft":
            refusals.append({
                "code": "copyleft-source", "file": name, "spdx": spdx,
                "reason": (
                    f'"{name}" declares {spdx}. Building it here and returning a bitstream '
                    "would convey a derivative work, which would make this service a "
                    "distributor of that licence. Build it locally instead — compiling for "
                    "yourself conveys nothing to anyone."),
                "evidence": evidence})
        elif family == "unknown":
            warnings.append({
                "code": "no-licence-declared", "file": name,
                "reason": (
                    f'"{name}" declares no licence this can read. That is not a refusal — '
                    "most HDL carries no notice — but nothing here has checked whether it "
                    "may be built on a shared server.")})
    return refusals, warnings
