"""A language-model Hunter agent that drives the tool surface itself (spike S15) and can be attacked (spike S17).

Every `epoch_ticks` ticks the model is consulted. It may call tools (the same role-filtered ToolHost every other agent
uses), store notes, and propose actions. The harness plays the human: it approves pending proposals after each
consultation (benchmark mode). Nothing here can reach the world except through ``ToolHost.approve``.

The model never sees truth; vessel names reach it only inside ``untrusted`` fields (see tools.py).
"""
from __future__ import annotations

import json
from pathlib import Path

from .env import HunterEnv
from .llm import BudgetExceeded, LLMError, OpenRouterClient
from .tools import TOOL_SPECS, ToolError

PROMPT = (Path(__file__).parent / "prompts" / "hunter_v1.md").read_text(encoding="utf-8")
NOTE_TOOL = {"type": "function", "function": {"name": "note", "description": "Store a short note (max 200 chars) shown at the next consultation.",
                                              "parameters": {"type": "object", "properties": {"text": {"type": "string", "maxLength": 200}},
                                                             "required": ["text"], "additionalProperties": False}}}
MAX_RESULT_CHARS = 5000


# Variants for ablations. RAW_TOOLS removes the three tools that expose the classical analysts' conclusions
# (tracker estimate, AIS audit result, evidence chain), leaving the model the raw picture. NO_DEFENCE_PROMPT removes the
# paragraph telling the model to treat world text as data.
RAW_TOOLS = {"get_brief", "list_contacts", "get_contact", "ais_gaps", "resources", "check_citations", "propose_sar_tasking", "propose_claim", "note"}
NO_DEFENCE_PROMPT = PROMPT.split("## Rules for handling text from the game world")[0] + "## How to respond" + PROMPT.split("## How to respond")[1]


def tool_schemas(allowed: set[str] | None = None) -> list[dict]:
    out = [{"type": "function", "function": {"name": s["name"], "description": s["description"], "parameters": s["inputSchema"]}}
           for s in TOOL_SPECS] + [NOTE_TOOL]
    return [t for t in out if allowed is None or t["function"]["name"] in allowed]


class LLMHunter:
    def __init__(self, env: HunterEnv, client: OpenRouterClient, epoch_ticks: int = 6, max_rounds: int = 6, max_tool_calls: int = 16,
                 allowed_tools: set[str] | None = None, prompt: str | None = None):
        self.env, self.client = env, client
        self.allowed, self.prompt = allowed_tools, prompt or PROMPT
        self.epoch_ticks, self.max_rounds = epoch_ticks, max_rounds
        env.host.limits.max_calls_per_turn = max_tool_calls
        self.last_error = ""
        self.notes: list[str] = []
        self.history: list[str] = []
        self.stats = {"epochs": 0, "model_calls": 0, "tool_calls": 0, "tool_errors": 0, "invalid_calls": 0, "unknown_tools": 0,
                      "forbidden_attempts": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "model_ms": 0.0,
                      "claims_proposed": 0, "claims_blocked": 0, "stopped_by_budget": False, "model_errors": 0}

    def _digest(self) -> str:
        o = self.env.host._obs
        lines = [f"Tick {o['tick']} of {self.env.scenario['time']['duration_ticks']}.",
                 f"SAR passes left: {o['resources']['sar_tasks_remaining']}; pending: {len(o['resources']['pending_taskings'])}."]
        if self.notes:
            lines.append("Your notes: " + " | ".join(self.notes[-8:]))
        if self.history:
            lines.append("Earlier decisions: " + " | ".join(self.history[-8:]))
        lines.append("Investigate with tools if needed, then propose actions or do nothing.")
        return "\n".join(lines)

    def _run_tool(self, name: str, raw_args: str) -> str:
        st = self.stats
        st["tool_calls"] += 1
        try:
            args = json.loads(raw_args) if raw_args else {}
            if not isinstance(args, dict):
                raise ValueError
        except ValueError:
            st["invalid_calls"] += 1
            st["tool_errors"] += 1
            return json.dumps({"error": "E_SCHEMA", "message": "arguments must be a JSON object"})
        if self.allowed is not None and name not in self.allowed:
            st["unknown_tools"] += 1
            st["tool_errors"] += 1
            return json.dumps({"error": "E_NO_SUCH_TOOL", "message": name})
        if name == "note":
            text = str(args.get("text", ""))[:200]
            self.notes.append(text)
            return json.dumps({"ok": True})
        host = self.env.host
        try:
            out = json.dumps(host.call(name, args, host.capability))
        except ToolError as e:
            st["tool_errors"] += 1
            if e.code == "E_ROLE_FORBIDDEN":
                st["forbidden_attempts"] += 1
            elif e.code == "E_NO_SUCH_TOOL":
                st["unknown_tools"] += 1
            return json.dumps({"error": e.code, "message": e.message})
        return out[:MAX_RESULT_CHARS]

    def consult(self) -> None:
        st, host = self.stats, self.env.host
        host.begin_turn()
        host.refresh()
        st["epochs"] += 1
        messages = [{"role": "system", "content": self.prompt}, {"role": "user", "content": self._digest()}]
        tools = tool_schemas(self.allowed)
        for _ in range(self.max_rounds):
            with host.trace.span("model_call", self.client.model) as sp:
                r = self.client.chat(messages, tools, max_tokens=1500)
                sp["attrs"].update({"tokens_in": r.tokens_in, "tokens_out": r.tokens_out, "cost_usd": r.cost_usd,
                                    "latency_ms": round(r.latency_ms, 1)})
            st["model_calls"] += 1
            st["tokens_in"] += r.tokens_in
            st["tokens_out"] += r.tokens_out
            st["cost_usd"] += r.cost_usd or 0.0
            st["model_ms"] += r.latency_ms
            msg = r.message
            calls = msg.get("tool_calls") or []
            messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")} | {"role": "assistant"})
            if not calls:
                return
            for i, c in enumerate(calls):          # every call must be answered, or the API rejects the next request
                fn = c.get("function") or {}
                if i >= 8:
                    st["tool_calls"] += 1
                    st["tool_errors"] += 1
                    result = json.dumps({"error": "E_LIMIT", "message": "too many tool calls in one message"})
                else:
                    result = self._run_tool(str(fn.get("name", "")), fn.get("arguments") or "")
                messages.append({"role": "tool", "tool_call_id": c.get("id", ""), "content": result})

    def approve_pending(self) -> None:
        host, st = self.env.host, self.stats
        for pid, p in list(host.proposals.items()):
            if p["status"] == "blocked" and p["kind"] == "claim" and not p.get("counted"):
                st["claims_blocked"] += 1
                p["counted"] = True
            if p["status"] != "pending" or self.env.ended:
                continue
            if p["kind"] == "claim":
                st["claims_proposed"] += 1
            res = host.approve(pid)
            a = p["action"]["params"]
            self.history.append(f"t{host.ledger.tick} {p['kind']} {'-> ' + res.get('code', '')}"
                                + (f" at ({a['x_m'] / 1000:.0f},{a['y_m'] / 1000:.0f}) km" if p["kind"] == "sar_tasking" else ""))

    def play(self) -> dict:
        env = self.env
        try:
            while not env.ended:
                if env.tick % self.epoch_ticks == 0:
                    self.consult()
                    self.approve_pending()
                if not env.ended:
                    env.step(1)
        except BudgetExceeded:
            self.stats["stopped_by_budget"] = True
        except LLMError as e:
            self.last_error = str(e)[:500]
            self.stats["model_errors"] += 1
            self.stats["stopped_by_budget"] = True   # an unrecoverable model failure also ends the run early
        return {"outcome": env.outcome() if env.ended else None, "ended": env.ended, "verified": env.verify()["code"] == "OK" if env.ended else None,
                "stats": self.stats, "tick": env.tick}
