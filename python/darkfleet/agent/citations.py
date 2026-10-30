"""Deterministic citation checker.

An agent finding is ``{"text": str, "cites": [contact_id], "assertions": [ {...} ]}``. Free text is never
trusted: only the structured assertions are checked, each against the evidence ledger, and
only ids the Hunter was actually shown count. Statuses:

  verified      cited, every id known, at least one assertion, every assertion holds
  uncited       no cited ids
  unknown_id    a cited id was never shown to this role (fabricated, or from another role's view)
  unsupported   an assertion is false against the cited evidence, or cites an id not in ``cites``
  unverifiable  cites are valid but nothing checkable was asserted (prose only; not "established")
  malformed     wrong shape or limits exceeded

Assertion types (the rule table, spike S14):
  position   {type, contact, x_m, y_m, tol_m[, at_tick]}   within tol_m + sigma of the reported position
  identity   {type, contact, mmsi}                          an AIS contact's claimed MMSI
  silent     {type, contact, at_least_ticks, as_of_tick}    AIS contact with no report for that long
  size_class {type, contact, size_class}                    a SAR contact's size class
  chain      {type, contacts: [sar ids]}                    time-ordered SAR detections one vessel could have made
  colocated  {type, ais, sar, tol_m}                        AIS report near a SAR detection (within 1 tick)
  apart      {type, ais, sar, min_m}                        AIS report at least min_m from a SAR detection (within 1 tick)
"""
from __future__ import annotations

import math

from .ledger import EvidenceLedger

V_MAX_MPS = 8.5
MAX_CITES, MAX_ASSERTIONS, MAX_TEXT = 16, 8, 600
ASSERTION_TYPES = ("position", "identity", "silent", "size_class", "chain", "colocated", "apart")


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


class CitationChecker:
    def __init__(self, ledger: EvidenceLedger):
        self.ledger = ledger

    # -- public -------------------------------------------------------------------------
    def check_report(self, findings) -> list[dict]:
        if not isinstance(findings, list):
            return [{"status": "malformed", "reasons": ["findings must be a list"]}]
        return [self.check_finding(f) for f in findings[:64]]

    def check_finding(self, f) -> dict:
        def out(status, *reasons):
            return {"status": status, "reasons": list(reasons)}

        if not isinstance(f, dict) or not isinstance(f.get("text"), str) or len(f["text"]) > MAX_TEXT:
            return out("malformed", "finding must be an object with a text string")
        cites, assertions = f.get("cites"), f.get("assertions", [])
        if not isinstance(cites, list) or not all(isinstance(c, str) for c in cites) or len(cites) > MAX_CITES:
            return out("malformed", "cites must be a list of at most 16 ids")
        if not isinstance(assertions, list) or len(assertions) > MAX_ASSERTIONS or not all(isinstance(a, dict) for a in assertions):
            return out("malformed", "assertions must be a list of at most 8 objects")
        if not cites:
            return out("uncited", "no evidence cited")
        unknown = [c for c in cites if self.ledger.get(c) is None]
        if unknown:
            return out("unknown_id", f"never shown to this role: {', '.join(unknown)}")
        if not assertions:
            return out("unverifiable", "nothing checkable was asserted")
        reasons = []
        for a in assertions:
            ok, why = self._check_assertion(a, set(cites))
            if not ok:
                reasons.append(why)
        return out("unsupported", *reasons) if reasons else out("verified")

    # -- rule table ---------------------------------------------------------------------
    def _check_assertion(self, a: dict, cites: set[str]) -> tuple[bool, str]:
        t = a.get("type")
        if t not in ASSERTION_TYPES:
            return False, f"unknown assertion type {t!r}"
        try:
            return getattr(self, f"_a_{t}")(a, cites)
        except (KeyError, TypeError, ValueError) as e:
            return False, f"malformed {t} assertion ({type(e).__name__})"

    def _rec(self, a: dict, key: str, cites: set[str]):
        cid = a[key]
        if not isinstance(cid, str):
            raise TypeError(key)
        if cid not in cites:
            return None, f"{key} {cid} is asserted about but not cited"
        return self.ledger.get(cid), ""

    def _a_position(self, a, cites):
        rec, why = self._rec(a, "contact", cites)
        if rec is None:
            return False, why
        if not (_num(a["x_m"]) and _num(a["y_m"]) and _num(a["tol_m"]) and 0 <= a["tol_m"] <= 20000):
            return False, "position needs numeric x_m, y_m and 0 <= tol_m <= 20000"
        if "at_tick" in a:
            if a["at_tick"] not in rec.history:
                return False, f"{rec.contact_id} has no report at tick {a['at_tick']}"
            x, y, s = rec.history[a["at_tick"]]
        else:
            x, y, s = rec.last_position()
        d = math.hypot(x - a["x_m"], y - a["y_m"])
        return (d <= a["tol_m"] + s), (f"reported position is {d:.0f} m away, tolerance {a['tol_m']:.0f} m" if d > a["tol_m"] + s else "")

    def _a_identity(self, a, cites):
        rec, why = self._rec(a, "contact", cites)
        if rec is None:
            return False, why
        if rec.claimed_mmsi is None:
            return False, f"{rec.contact_id} is unlabeled (no MMSI)"
        return (rec.claimed_mmsi == a["mmsi"]), ("" if rec.claimed_mmsi == a["mmsi"] else "MMSI does not match the contact")

    def _a_silent(self, a, cites):
        rec, why = self._rec(a, "contact", cites)
        if rec is None:
            return False, why
        if rec.source != "ais":
            return False, "only AIS contacts can be silent"
        n, as_of = a["at_least_ticks"], a["as_of_tick"]
        if not (isinstance(n, int) and isinstance(as_of, int) and n >= 1 and as_of <= self.ledger.tick):
            return False, "silent needs integer at_least_ticks >= 1 and as_of_tick not in the future"
        silent = as_of - rec.last_tick()
        return (silent >= n), ("" if silent >= n else f"last report was {silent} tick(s) before tick {as_of}")

    def _a_size_class(self, a, cites):
        rec, why = self._rec(a, "contact", cites)
        if rec is None:
            return False, why
        return (rec.size_class == a["size_class"]), ("" if rec.size_class == a["size_class"] else "size class does not match")

    def _a_chain(self, a, cites):
        ids = a["contacts"]
        if not (isinstance(ids, list) and 2 <= len(ids) <= 8 and len(set(ids)) == len(ids)):
            return False, "chain needs 2-8 distinct contact ids"
        pts = []
        for cid in ids:
            if cid not in cites:
                return False, f"chain member {cid} is not cited"
            rec = self.ledger.get(cid)
            if rec is None or rec.source != "sar":
                return False, f"chain member {cid} is not a SAR detection"
            x, y, s = rec.last_position()
            pts.append((rec.last_tick(), x, y, s))
        pts.sort()
        for (t1, x1, y1, s1), (t2, x2, y2, s2) in zip(pts, pts[1:]):
            if t2 <= t1:
                return False, "chain detections share a tick (one vessel cannot be in two places)"
            reach = V_MAX_MPS * (t2 - t1) * self.ledger.tick_s + 3 * (s1 + s2)
            if math.hypot(x2 - x1, y2 - y1) > reach:
                return False, f"detections at ticks {t1} and {t2} are farther apart than a vessel can travel"
        return True, ""

    def _a_colocated(self, a, cites):
        ais, why = self._rec(a, "ais", cites)
        if ais is None:
            return False, why
        sar, why = self._rec(a, "sar", cites)
        if sar is None:
            return False, why
        if ais.source != "ais" or sar.source != "sar" or not (_num(a["tol_m"]) and 0 <= a["tol_m"] <= 20000):
            return False, "colocated needs an AIS id, a SAR id and 0 <= tol_m <= 20000"
        ts, xs, ys, ss = sar.last_tick(), *sar.last_position()
        best = None
        for t, (x, y, _) in ais.history.items():
            if abs(t - ts) <= 1:
                d = math.hypot(x - xs, y - ys)
                best = d if best is None else min(best, d)
        if best is None:
            return False, "the AIS contact has no report within 1 tick of the SAR detection"
        return (best <= a["tol_m"] + ss), ("" if best <= a["tol_m"] + ss else f"AIS and SAR are {best:.0f} m apart")

    def _a_apart(self, a, cites):
        ais, why = self._rec(a, "ais", cites)
        if ais is None:
            return False, why
        sar, why = self._rec(a, "sar", cites)
        if sar is None:
            return False, why
        if ais.source != "ais" or sar.source != "sar" or not (_num(a["min_m"]) and 0 <= a["min_m"] <= 400000):
            return False, "apart needs an AIS id, a SAR id and 0 <= min_m <= 400000"
        ts, xs, ys, ss = sar.last_tick(), *sar.last_position()
        ds = [math.hypot(x - xs, y - ys) for t, (x, y, _) in ais.history.items() if abs(t - ts) <= 1]
        if not ds:
            return False, "the AIS contact has no report within 1 tick of the SAR detection"
        d = min(ds)
        return (d - ss >= a["min_m"]), ("" if d - ss >= a["min_m"] else f"AIS and SAR are only {d:.0f} m apart")

    # -- claim gate ---------------------------------------------------------------------
    def claim_gate(self, contact_id: str, evidence: list[str]) -> tuple[bool, list[str]]:
        """Deterministic gate for ``file_claim`` proposals. Blocks on ids the Hunter was never shown
        or cannot currently see. It does not judge the claim's merit; the engine does that."""
        problems = []
        for cid in dict.fromkeys([contact_id, *evidence]):
            if self.ledger.get(cid) is None:
                problems.append(f"{cid}: never shown to this role")
            elif cid not in self.ledger.visible_now:
                problems.append(f"{cid}: not currently visible (the engine will refuse it)")
        return (not problems), problems
