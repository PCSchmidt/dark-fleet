"""Case Officer: an explicit plan -> delegate -> verify -> present state machine.

Specialists are deterministic planners over the tool surface:
    AIS Analyst         reads the brief, tracker and AIS gaps; reports what is checkable
    Collection Planner  proposes SAR taskings (an early audit pass, then passes where the tracker expects the target)
    Evidence Clerk      links SAR detections into a chain and proposes a claim only when there is a basis to accuse

A model, when configured, writes the closing explanation from *verified* findings only. Every
finding goes through the citation checker; unverified ones are shown but labelled.
"""
from __future__ import annotations

import json

from .llm import LLM, NullLLM, SYSTEM_PROMPT
from .tools import ToolError, ToolHost

DARK_EVIDENCE_TICKS = 6   # the target must have been silent this long before a claim is proposed
STATUS_LABEL = {
    "verified": "verified",
    "uncited": "UNVERIFIED: no evidence cited",
    "unknown_id": "UNVERIFIED: cites evidence this role was never shown",
    "unsupported": "UNVERIFIED: assertion contradicted by the evidence",
    "unverifiable": "UNVERIFIED: prose only, nothing checkable",
    "malformed": "UNVERIFIED: malformed",
}


class CaseOfficer:
    def __init__(self, host: ToolHost, llm: LLM | None = None):
        self.host = host
        self.llm = llm or NullLLM()

    # -- tool access with error capture ---------------------------------------------------------
    def _tool(self, name: str, args: dict | None = None) -> dict | None:
        try:
            return self.host.call(name, args or {}, self.host.capability)
        except ToolError as e:
            self.notes.append(f"{name} failed: {e.code}")
            return None

    # -- specialists ----------------------------------------------------------------------------
    def _ais_analyst(self) -> list[dict]:
        findings = []
        est = self._tool("target_estimate")
        if not est:
            return findings
        mmsi = est.get("locked_mmsi")
        if not mmsi:
            self.notes.append("No AIS vessel is close enough to the last-known area to name a candidate yet.")
            return findings
        cid = self.host.ledger.by_mmsi.get(mmsi)
        rec = self.host.ledger.get(cid) if cid else None
        if rec is None:
            return findings
        x, y, _ = rec.last_position()
        findings.append({"text": f"Candidate target: AIS vessel {mmsi}, the one nearest the last-known area when first seen.",
                         "cites": [cid],
                         "assertions": [{"type": "identity", "contact": cid, "mmsi": mmsi},
                                        {"type": "position", "contact": cid, "x_m": x, "y_m": y, "tol_m": 100.0}]})
        silent = est.get("target_silent_ticks") or 0
        if silent >= 3:
            findings.append({"text": f"The candidate has been silent on AIS for {silent} ticks (last report tick {rec.last_tick()}).",
                             "cites": [cid],
                             "assertions": [{"type": "silent", "contact": cid, "at_least_ticks": silent,
                                             "as_of_tick": self.host.ledger.tick}]})
        return findings

    def _collection_planner(self) -> list[dict]:
        proposals = []
        res = self._tool("resources")
        est = self._tool("target_estimate")
        if not res or not est or res["sar_tasks_remaining"] < 1 or res["pending_taskings"]:
            return proposals
        silent = est.get("target_silent_ticks")
        pf = est.get("particle_filter")
        tick = self.host.ledger.tick
        audit = self._tool("audit_status") or {}
        an = self.host.analyst
        spacing = tick - an.last_task_tick
        if audit.get("state") == "spoof_suspected":
            fix = an._target_fix_near(tick)
            if fix is not None and spacing >= 4 and audit["offset_m"]:
                proposals.append(self._tool("propose_sar_tasking", {
                    "x_m": fix[0] + audit["offset_m"]["x_m"], "y_m": fix[1] + audit["offset_m"]["y_m"],
                    "rationale": "AIS position looks spoofed; follow the vessel at the broadcast position plus the measured offset."}))
        elif spacing < 6 or audit.get("state") == "pending":
            pass
        elif silent is not None and silent >= 2 and pf:
            proposals.append(self._tool("propose_sar_tasking", {
                "x_m": pf["x_m"], "y_m": pf["y_m"],
                "rationale": f"Candidate silent {silent} ticks; particle filter puts it here (spread {pf['spread_m'] / 1000:.0f} km)."}))
        elif silent is not None and silent <= 1 and tick >= 8 and not self.host.audit_proposed:
            mmsi = est["locked_mmsi"]
            rec = self.host.ledger.get(self.host.ledger.by_mmsi[mmsi])
            x, y, _ = rec.last_position()
            self.host.audit_proposed = True
            p = self._tool("propose_sar_tasking", {
                "x_m": x, "y_m": y,
                "rationale": "Audit pass: check that a large vessel is really under the target's broadcast AIS position."})
            if p:
                self.host.proposals[p["id"]]["purpose"] = "audit"
                proposals.append(self.host.proposals[p["id"]])
        return [p for p in proposals if p]

    def _evidence_clerk(self) -> tuple[list[dict], list[dict]]:
        findings, proposals = [], []
        ch = self._tool("evidence_chain")
        if not ch or len(ch["contacts"]) < 2 or not ch["consistent"]:
            return findings, proposals
        ids = ch["contacts"]
        findings.append({"text": f"{len(ids)} SAR detections are consistent with one large vessel moving at ship speeds.",
                         "cites": ids,
                         "assertions": [{"type": "chain", "contacts": ids}, {"type": "size_class", "contact": ids[0], "size_class": "large"}]})
        if ch.get("basis") == "spoof_offset":
            an = self.host.analyst
            ais_cid = self.host.ledger.by_mmsi.get(an.locked_mmsi or "")
            off = (self._tool("audit_status") or {}).get("offset_m") or {"x_m": 0.0, "y_m": 0.0}
            d = (off["x_m"] ** 2 + off["y_m"] ** 2) ** 0.5
            if ais_cid:
                findings.append({"text": f"At the audit pass the target's AIS position had no vessel under it; a large vessel sat about {d / 1000:.0f} km away.",
                                 "cites": [ais_cid, ids[0]],
                                 "assertions": [{"type": "apart", "ais": ais_cid, "sar": ids[0], "min_m": d * 0.5}]})
            p = self._tool("propose_claim", {
                "contact_id": ids[-1], "evidence": ids,
                "rationale": "AIS position is false: the vessel under it is elsewhere, and two SAR passes confirm its track."})
            if p:
                proposals.append(p)
        elif self.host.max_silent >= DARK_EVIDENCE_TICKS:
            p = self._tool("propose_claim", {
                "contact_id": ids[-1], "evidence": ids,
                "rationale": f"Target went dark for up to {self.host.max_silent} ticks and the SAR chain agrees with the tracker."})
            if p:
                proposals.append(p)
        else:
            self.notes.append("A SAR chain exists, but the candidate has not been dark long enough to justify an accusation.")
        return findings, proposals

    # -- the loop ---------------------------------------------------------------------------------
    def recommend(self) -> dict:
        h = self.host
        h.begin_turn()
        h.refresh()
        self.notes: list[str] = []
        start = len(h.trace.spans)
        steps = []
        with h.trace.span("agent_step", "case_officer.recommend", tick=h.ledger.tick):
            steps.append("plan: brief -> track -> collect -> link evidence -> verify -> present")
            with h.trace.span("agent_step", "delegate.ais_analyst"):
                findings = self._ais_analyst()
                steps.append(f"ais_analyst: {len(findings)} finding(s)")
            with h.trace.span("agent_step", "delegate.collection_planner"):
                proposals = self._collection_planner()
                steps.append(f"collection_planner: {len(proposals)} proposal(s)")
            with h.trace.span("agent_step", "delegate.evidence_clerk"):
                f2, p2 = self._evidence_clerk()
                findings += f2
                proposals += p2
                steps.append(f"evidence_clerk: {len(f2)} finding(s), {len(p2)} proposal(s)")
            with h.trace.span("agent_step", "verify.citations"):
                results = (self._tool("check_citations", {"findings": findings}) or {"results": []})["results"] if findings else []
                for f, r in zip(findings, results):
                    f["status"], f["status_label"], f["reasons"] = r["status"], STATUS_LABEL[r["status"]], r["reasons"]
                steps.append(f"verify: {sum(1 for f in findings if f.get('status') == 'verified')}/{len(findings)} verified")
            summary = self._summarise(findings, proposals)
        spans = h.trace.spans[start:]
        return {"tick": h.ledger.tick, "steps": steps, "notes": self.notes, "findings": findings, "proposals": proposals,
                "summary": summary, "trace": spans, "totals": _totals(spans)}

    def _summarise(self, findings: list[dict], proposals: list[dict]) -> dict:
        verified = [f for f in findings if f.get("status") == "verified"]
        template = " ".join(f["text"] for f in verified) or "Nothing verified yet."
        if proposals:
            template += " Proposals: " + "; ".join(f"{p['kind']} ({p['status']})" for p in proposals) + "."
        out = {"text": template, "source": "template"}
        if isinstance(self.llm, NullLLM):
            return out
        facts = {"verified_findings": [{"text": f["text"], "cites": f["cites"]} for f in verified],
                 "proposals": [{"id": p["id"], "kind": p["kind"], "status": p["status"],
                                "rationale_untrusted": p["rationale_untrusted"]} for p in proposals]}
        user = "<facts>\n" + json.dumps(facts) + "\n</facts>"
        with self.host.trace.span("model_call", getattr(self.llm, "name", "llm")) as sp:
            res = self.llm.complete(SYSTEM_PROMPT, user, 300)
            if res is None:
                sp["attrs"]["skipped"] = True
                return out
            sp["attrs"].update({"tokens_in": res.tokens_in, "tokens_out": res.tokens_out, "cost_usd": res.cost_usd,
                                "model": res.model, "latency_ms": round(res.latency_ms, 1)})
        return {"text": res.text, "source": "model", "model": res.model, "checked": False,
                "note": "model-written prose; only the structured findings above are verified"}


def _totals(spans: list[dict]) -> dict:
    from .trace import Trace
    t = Trace()
    t.spans = spans
    return t.totals()
