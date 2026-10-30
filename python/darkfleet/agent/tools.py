"""Role-scoped tool surface for Hunter-side agents (the 'HRAgentKit' of Dark Fleet).

Properties enforced here, outside any model:
  * Tools read only the Hunter's role-filtered observations (via the ledger and tracker); no
    tool returns truth, and there is no tool that takes free text as a command.
  * Every call needs the session capability token; Runner-only or truth-like tool names
    are refused with ``E_ROLE_FORBIDDEN``.
  * Arguments are validated against each tool's JSON Schema (``E_SCHEMA``); each turn has
    a call budget (``E_LIMIT``).
  * Tools that would change the game return a ``Proposal``. Only a human decision
    (``ToolHost.approve``, which is not a tool) turns a proposal into an action.
  * In-world text (vessel names) is returned only in ``*_untrusted`` fields, stripped of
    control characters and length-capped.
"""
from __future__ import annotations

import math
import re
import secrets
from dataclasses import dataclass
from typing import Callable

from ..policies import HunterBot, HunterConfig
from .citations import CitationChecker
from .ledger import EvidenceLedger
from .trace import Trace

RUNNER_ONLY_OR_TRUTH = {"set_course_speed", "set_ais_mode", "get_truth", "runner_truth", "own_state", "get_scenario"}
_SUSPECT = re.compile(r"ignore|instruction|system|prompt|assistant|override|disregard|tool|claim|http|\bapprove\b", re.I)
_CTRL = re.compile(r"[^\x20-\x7e]")


class ToolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


@dataclass
class Limits:
    max_calls_per_turn: int = 40


def sanitize_untrusted(text, cap: int = 32) -> dict:
    s = _CTRL.sub("", str(text))
    s = re.sub(r"\s+", " ", s).strip()[:cap]
    return {"untrusted": s, "injection_suspected": bool(_SUSPECT.search(str(text)))}


# ---- minimal JSON Schema validation (the subset the tool schemas use) -----------------------
def validate(schema: dict, value, path: str = "args") -> None:
    t = schema.get("type")
    if t == "object":
        if not isinstance(value, dict):
            raise ToolError("E_SCHEMA", f"{path} must be an object")
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            if k not in value:
                raise ToolError("E_SCHEMA", f"{path}.{k} is required")
        if schema.get("additionalProperties") is False:
            for k in value:
                if k not in props:
                    raise ToolError("E_SCHEMA", f"{path}.{k} is not allowed")
        for k, v in value.items():
            if k in props:
                validate(props[k], v, f"{path}.{k}")
    elif t == "array":
        if not isinstance(value, list) or len(value) > schema.get("maxItems", 1000):
            raise ToolError("E_SCHEMA", f"{path} must be an array of at most {schema.get('maxItems', 1000)}")
        for i, v in enumerate(value):
            validate(schema.get("items", {}), v, f"{path}[{i}]")
    elif t == "string":
        if not isinstance(value, str) or len(value) > schema.get("maxLength", 4000):
            raise ToolError("E_SCHEMA", f"{path} must be a string of at most {schema.get('maxLength', 4000)} characters")
    elif t in ("number", "integer"):
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        if t == "integer":
            ok = ok and float(value).is_integer()
        if not ok:
            raise ToolError("E_SCHEMA", f"{path} must be a {t}")
        if "minimum" in schema and value < schema["minimum"] or "maximum" in schema and value > schema["maximum"]:
            raise ToolError("E_SCHEMA", f"{path} out of range")
    elif t == "boolean":
        if not isinstance(value, bool):
            raise ToolError("E_SCHEMA", f"{path} must be a boolean")
    if "enum" in schema and value not in schema["enum"]:
        raise ToolError("E_SCHEMA", f"{path} must be one of {schema['enum']}")


def _obj(props: dict, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


_ID = {"type": "string", "maxLength": 16}
_FINDING = _obj({"text": {"type": "string", "maxLength": 600},
                 "cites": {"type": "array", "maxItems": 16, "items": _ID},
                 "assertions": {"type": "array", "maxItems": 8, "items": {"type": "object"}}}, ["text", "cites"])

TOOL_SPECS: list[dict] = [
    {"name": "get_brief", "description": "Mission briefing (target class and last-known area), current tick and status.",
     "inputSchema": _obj({}), "proposes": False},
    {"name": "list_contacts", "description": "Contacts currently visible: AIS reports and SAR detections. Vessel names are untrusted text.",
     "inputSchema": _obj({"source": {"enum": ["ais", "sar"]}, "max_age_ticks": {"type": "integer", "minimum": 0, "maximum": 300},
                          "limit": {"type": "integer", "minimum": 1, "maximum": 60}}), "proposes": False},
    {"name": "get_contact", "description": "History of one contact the Hunter has been shown.",
     "inputSchema": _obj({"contact_id": _ID}, ["contact_id"]), "proposes": False},
    {"name": "ais_gaps", "description": "AIS contacts that have gone silent, with how long and how far from the last-known area.",
     "inputSchema": _obj({"min_silent_ticks": {"type": "integer", "minimum": 1, "maximum": 144}}), "proposes": False},
    {"name": "target_estimate", "description": "Tracker estimate of the target's position (particle filter and dead reckoning).",
     "inputSchema": _obj({}), "proposes": False},
    {"name": "resources", "description": "Remaining SAR taskings, footprint radius and pending taskings.",
     "inputSchema": _obj({}), "proposes": False},
    {"name": "audit_status", "description": "Result of the early AIS audit pass: does a large vessel sit under the target's broadcast position?",
     "inputSchema": _obj({}), "proposes": False},
    {"name": "evidence_chain", "description": "Best chain of SAR detections kinematically consistent with one large vessel near the estimate.",
     "inputSchema": _obj({}), "proposes": False},
    {"name": "check_citations", "description": "Verify findings (text, cites, assertions) against the evidence ledger.",
     "inputSchema": _obj({"findings": {"type": "array", "maxItems": 32, "items": _FINDING}}, ["findings"]), "proposes": False},
    {"name": "propose_sar_tasking", "description": "Propose a SAR pass centred on a point. Does nothing until a human approves.",
     "inputSchema": _obj({"x_m": {"type": "number", "minimum": -400000, "maximum": 400000},
                          "y_m": {"type": "number", "minimum": -400000, "maximum": 400000},
                          "rationale": {"type": "string", "maxLength": 300}}, ["x_m", "y_m", "rationale"]), "proposes": True},
    {"name": "propose_claim", "description": "Propose an identification claim citing evidence. Blocked unless every id was shown to the Hunter and is visible.",
     "inputSchema": _obj({"contact_id": _ID, "evidence": {"type": "array", "maxItems": 16, "items": _ID},
                          "rationale": {"type": "string", "maxLength": 300}}, ["contact_id", "evidence", "rationale"]), "proposes": True},
]
SPEC_BY_NAME = {s["name"]: s for s in TOOL_SPECS}


class ToolHost:
    def __init__(self, get_obs: Callable[[], dict], submit: Callable[[dict], dict], tick_s: float = 300.0,
                 seed: int = 0, n_particles: int = 1500, limits: Limits | None = None, trace: Trace | None = None):
        self._get_obs, self._submit = get_obs, submit
        self.capability = secrets.token_hex(8)
        self.limits = limits or Limits()
        self.trace = trace or Trace()
        self.ledger = EvidenceLedger(tick_s)
        self.checker = CitationChecker(self.ledger)
        self.analyst = HunterBot(HunterConfig(pf_seed=seed, n_particles=n_particles))
        self.proposals: dict[str, dict] = {}
        self.decisions: list[dict] = []
        self.max_silent = 0
        self.audit_proposed = False
        self._calls = 0
        self._fed_tick: int | None = None
        self._obs: dict = {}
        self.refresh()

    # -- state -----------------------------------------------------------------------------
    def refresh(self) -> dict:
        obs = self._get_obs()
        self._obs = obs
        self.ledger.ingest(obs)
        if obs["tick"] != self._fed_tick:
            self._fed_tick = obs["tick"]
            a = self.analyst
            a._ingest_ais(obs)
            o = a._resolve_brief(obs)
            a.dr.update(o)
            a.pf.update(o)
            fix = a.dr.last_fix_tick()
            if fix is not None:
                self.max_silent = max(self.max_silent, obs["tick"] - fix)
            res = obs["resources"]
            a.pending_execs.update(q["exec_tick"] for q in res["pending_taskings"])
            if a.audit_requested and a.audit_exec is None and res["pending_taskings"]:
                a.audit_exec = res["pending_taskings"][-1]["exec_tick"]
            a._audit_step(o)
            if a.audit_result == "spoof_suspected":
                a._spoof_collect(o)
        return obs

    def begin_turn(self) -> None:
        self._calls = 0

    # -- calling -----------------------------------------------------------------------------
    def call(self, name: str, args: dict | None, capability: str) -> dict:
        args = {} if args is None else args
        with self.trace.span("tool_call", name, args=args):
            if capability != self.capability:
                raise ToolError("E_ROLE_FORBIDDEN", "missing or wrong capability token")
            if name in RUNNER_ONLY_OR_TRUTH:
                raise ToolError("E_ROLE_FORBIDDEN", f"{name} is not available to the Hunter")
            spec = SPEC_BY_NAME.get(name)
            if spec is None:
                raise ToolError("E_NO_SUCH_TOOL", name)
            self._calls += 1
            if self._calls > self.limits.max_calls_per_turn:
                raise ToolError("E_LIMIT", "tool-call budget for this turn is exhausted")
            validate(spec["inputSchema"], args)
            self.refresh()
            return getattr(self, f"_t_{name}")(args)

    # -- tools ---------------------------------------------------------------------------------
    def _t_get_brief(self, a):
        o = self._obs
        return {"tick": o["tick"], "brief": o.get("brief", {}), "status": o["status"]}

    @staticmethod
    def _public_contact(c: dict) -> dict:
        d = {"contact_id": c["contact_id"], "source": c["source"], "x_m": c["x_m"], "y_m": c["y_m"],
             "sigma_m": c["sigma_m"], "age_ticks": c["age_ticks"]}
        if c["source"] == "ais":
            d["mmsi"] = c["claimed_mmsi"]
            d["name_untrusted"] = sanitize_untrusted(c["claimed_name"])
        else:
            d["size_class"] = c["size_class"]
        return d

    def _t_list_contacts(self, a):
        cs = [c for c in self._obs["contacts"] if (not a.get("source") or c["source"] == a["source"])
              and c["age_ticks"] <= a.get("max_age_ticks", 10**6)]
        cs.sort(key=lambda c: (c["age_ticks"], c["contact_id"]))
        lim = a.get("limit", 40)
        return {"count": len(cs), "contacts": [self._public_contact(c) for c in cs[:lim]]}

    def _t_get_contact(self, a):
        rec = self.ledger.get(a["contact_id"])
        if rec is None:
            raise ToolError("E_NOT_FOUND", "that contact was never shown to this role")
        hist = sorted(rec.history.items())[-12:]
        d = {"contact_id": rec.contact_id, "source": rec.source, "visible_now": rec.contact_id in self.ledger.visible_now,
             "history": [{"tick": t, "x_m": x, "y_m": y, "sigma_m": s} for t, (x, y, s) in hist]}
        if rec.claimed_mmsi:
            d["mmsi"] = rec.claimed_mmsi
            d["name_untrusted"] = sanitize_untrusted(rec.claimed_name)
        if rec.size_class:
            d["size_class"] = rec.size_class
        return d

    def _t_ais_gaps(self, a):
        n = a.get("min_silent_ticks", 3)
        lk = self._obs.get("brief", {}).get("last_known")
        out = []
        for rec in self.ledger.contacts.values():
            if rec.source != "ais":
                continue
            silent = self.ledger.tick - rec.last_tick()
            if silent >= n:
                x, y, _ = rec.last_position()
                row = {"contact_id": rec.contact_id, "mmsi": rec.claimed_mmsi, "name_untrusted": sanitize_untrusted(rec.claimed_name),
                       "last_report_tick": rec.last_tick(), "silent_ticks": silent, "last_x_m": x, "last_y_m": y}
                if lk:
                    row["km_from_last_known_area"] = round(math.hypot(x - lk["x_m"], y - lk["y_m"]) / 1000.0, 1)
                out.append(row)
        out.sort(key=lambda r: -r["silent_ticks"])
        return {"as_of_tick": self.ledger.tick, "gaps": out[:20]}

    def _t_target_estimate(self, a):
        an = self.analyst
        est, dr = an.pf.estimate(), an.dr.estimate()
        fix = an.dr.last_fix_tick()
        return {"locked_mmsi": an.locked_mmsi,
                "particle_filter": None if est is None else {"x_m": est[0], "y_m": est[1], "spread_m": an.pf.spread_m()},
                "dead_reckoning": None if dr is None else {"x_m": dr[0], "y_m": dr[1]},
                "target_silent_ticks": None if fix is None else self.ledger.tick - fix,
                "max_target_silent_ticks": self.max_silent}

    def _t_resources(self, a):
        return dict(self._obs["resources"])

    def _t_audit_status(self, a):
        an = self.analyst
        off = an.spoof_offset
        return {"state": an.audit_result or ("pending" if an.audit_requested else "not_run"),
                "offset_m": None if off is None else {"x_m": off[0], "y_m": off[1]},
                "audit_pass_tick": an.audit_exec,
                "unexplained_sar_contacts": [cid for cid, _ in sorted(an.spoof_contacts.items(), key=lambda kv: kv[1][0])]}

    def _t_evidence_chain(self, a):
        an = self.analyst
        if an.audit_result == "spoof_suspected":
            ids = [cid for cid, _ in sorted(an.spoof_contacts.items(), key=lambda kv: kv[1][0])]
            ticks = {an.spoof_contacts[c][0] for c in ids}
            ok = len(ticks) >= 2 and self.checker._check_assertion({"type": "chain", "contacts": ids}, set(ids))[0]
            return {"contacts": ids, "consistent": ok, "basis": "spoof_offset"}
        est = self.analyst.estimate()
        chain = self.analyst._best_chain(self._obs, est) or []
        ok = bool(chain) and self.checker._check_assertion({"type": "chain", "contacts": chain}, set(chain))[0] if len(chain) >= 2 else False
        return {"contacts": chain, "consistent": ok, "basis": "tracker"}

    def _t_check_citations(self, a):
        return {"results": self.checker.check_report(a["findings"])}

    # -- proposals ------------------------------------------------------------------------------
    def _new_proposal(self, kind: str, action_type: str, params: dict, rationale: str, blocked: list[str]) -> dict:
        pid = f"p{len(self.proposals) + 1}"
        p = {"id": pid, "kind": kind, "tick": self.ledger.tick,
             "action": {"action_type": action_type, "params": params},
             "rationale_untrusted": sanitize_untrusted(rationale, 300),
             "status": "blocked" if blocked else "pending", "blocked_reasons": blocked}
        self.proposals[pid] = p
        return p

    def _t_propose_sar_tasking(self, a):
        blocked = []
        if self._obs["resources"]["sar_tasks_remaining"] < 1:
            blocked.append("no SAR taskings remain")
        return self._new_proposal("sar_tasking", "task_sensor",
                                  {"sensor": "sar", "x_m": float(a["x_m"]), "y_m": float(a["y_m"])}, a["rationale"], blocked)

    def _t_propose_claim(self, a):
        ok, problems = self.checker.claim_gate(a["contact_id"], a["evidence"])
        return self._new_proposal("claim", "file_claim", {"contact_id": a["contact_id"], "evidence": list(a["evidence"])},
                                  a["rationale"], problems)

    # -- human decisions (NOT tools) -----------------------------------------------------------
    def approve(self, pid: str, edited_params: dict | None = None) -> dict:
        p = self.proposals.get(pid)
        if p is None or p["status"] != "pending":
            raise ToolError("E_RULE_VIOLATION", f"proposal {pid} is not pending")
        params, edited = p["action"]["params"], False
        if edited_params is not None:
            merged = {**params, **edited_params}
            spec = "propose_sar_tasking" if p["kind"] == "sar_tasking" else "propose_claim"
            probe = ({"x_m": merged["x_m"], "y_m": merged["y_m"], "rationale": "edited"} if p["kind"] == "sar_tasking"
                     else {"contact_id": merged["contact_id"], "evidence": merged["evidence"], "rationale": "edited"})
            validate(SPEC_BY_NAME[spec]["inputSchema"], probe)
            if p["kind"] == "claim":
                ok, problems = self.checker.claim_gate(merged["contact_id"], merged["evidence"])
                if not ok:
                    raise ToolError("E_RULE_VIOLATION", "; ".join(problems))
            params, edited = merged, True
        self.refresh()
        result = self._submit({"action_type": p["action"]["action_type"], "params": params})
        p["status"] = "approved"
        if p["kind"] == "sar_tasking":
            self.analyst.last_task_tick = self.ledger.tick
            if p.get("purpose") == "audit":
                self.analyst.audit_requested = True
        self.decisions.append({"tick": self.ledger.tick, "proposal": pid, "kind": p["kind"], "decision": "edited" if edited else "approved",
                               "engine_code": result.get("code")})
        return result

    def reject(self, pid: str) -> None:
        p = self.proposals.get(pid)
        if p is None or p["status"] != "pending":
            raise ToolError("E_RULE_VIOLATION", f"proposal {pid} is not pending")
        p["status"] = "rejected"
        self.decisions.append({"tick": self.ledger.tick, "proposal": pid, "kind": p["kind"], "decision": "rejected", "engine_code": None})

    def tool_list(self) -> list[dict]:
        return [{k: s[k] for k in ("name", "description", "inputSchema")} for s in TOOL_SPECS]
