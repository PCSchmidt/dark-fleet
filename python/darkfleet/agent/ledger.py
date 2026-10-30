"""Evidence ledger: everything the Hunter has *ever been shown*, keyed by contact id.

Built only from Hunter observation documents. The citation checker treats an id the ledger has
never seen as fabricated or out-of-role, which is exactly what it is from the Hunter's side:
contact ids are salted per role, so ids from the Runner's view cannot collide with these.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ContactRecord:
    contact_id: str
    source: str                                   # "ais" | "sar"
    claimed_mmsi: str | None = None
    claimed_name: str | None = None               # untrusted free text
    size_class: str | None = None
    history: dict[int, tuple[float, float, float]] = field(default_factory=dict)  # tick -> (x, y, sigma)

    def last_tick(self) -> int:
        return max(self.history)

    def last_position(self) -> tuple[float, float, float]:
        return self.history[self.last_tick()]


class EvidenceLedger:
    def __init__(self, tick_s: float = 300.0):
        self.tick_s = tick_s
        self.contacts: dict[str, ContactRecord] = {}
        self.by_mmsi: dict[str, str] = {}
        self.tick = 0
        self.visible_now: set[str] = set()

    def ingest(self, obs: dict) -> None:
        self.tick = obs["tick"]
        vis: set[str] = set()
        for c in obs["contacts"]:
            cid = c["contact_id"]
            vis.add(cid)
            rec = self.contacts.get(cid)
            if rec is None:
                rec = ContactRecord(cid, c["source"], c.get("claimed_mmsi"), c.get("claimed_name"), c.get("size_class"))
                self.contacts[cid] = rec
                if rec.claimed_mmsi:
                    self.by_mmsi[rec.claimed_mmsi] = cid
            rec.history[obs["tick"] - c["age_ticks"]] = (c["x_m"], c["y_m"], c["sigma_m"])
        self.visible_now = vis

    def get(self, cid: str) -> ContactRecord | None:
        return self.contacts.get(cid)
