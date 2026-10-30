"""Structured agent traces (AGENT_TRACE).

Spans are plain dicts so they serialise to JSONL and map onto OpenTelemetry-style spans later.
The trace is observability only: replays are driven by recorded inputs, never by this trace.
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class Trace:
    spans: list[dict] = field(default_factory=list)
    _stack: list[int] = field(default_factory=list)

    @contextmanager
    def span(self, kind: str, name: str, **attrs):
        sp = {"id": len(self.spans), "parent": self._stack[-1] if self._stack else None, "kind": kind,
              "name": name, "attrs": dict(attrs), "ms": 0.0, "error": None}
        self.spans.append(sp)
        self._stack.append(sp["id"])
        t0 = time.perf_counter()
        try:
            yield sp
        except Exception as e:
            sp["error"] = f"{type(e).__name__}: {e}"
            raise
        finally:
            sp["ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
            self._stack.pop()

    def totals(self) -> dict:
        tool = [s for s in self.spans if s["kind"] == "tool_call"]
        model = [s for s in self.spans if s["kind"] == "model_call"]
        cost = [s["attrs"].get("cost_usd") for s in model if s["attrs"].get("cost_usd") is not None]
        return {
            "spans": len(self.spans),
            "tool_calls": len(tool),
            "tool_errors": sum(1 for s in tool if s["error"]),
            "model_calls": len(model),
            "tokens_in": sum(s["attrs"].get("tokens_in", 0) for s in model),
            "tokens_out": sum(s["attrs"].get("tokens_out", 0) for s in model),
            "cost_usd": round(sum(cost), 6) if cost else None,   # None = not priced, not "free"
            "wall_ms": round(sum(s["ms"] for s in self.spans if s["parent"] is None), 3),
        }

    def to_jsonl(self) -> str:
        return "\n".join(json.dumps(s, sort_keys=True) for s in self.spans)

    def clear(self) -> None:
        self.spans.clear()
        self._stack.clear()
