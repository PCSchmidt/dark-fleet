"""Guided-practice coaches for the playable client.

Each coach is a function of the human's own role-filtered observation (plus a little memory of what it has
already pointed out). It never reads simulator truth, so following it is not cheating and cannot leak anything
the real player would not see. It returns one step at a time: what is going on, what to do now, and (where useful)
a button that performs the suggested action on the player's behalf. The player still decides when to press it.
"""
from __future__ import annotations

import math

STOP_M = 300.0            # an AIS marker that moved less than this between two reports has stopped (a moving tanker covers ~2 km a tick)
READY_TICKS = 7           # ticks the target must have been stopped before the coach says "file the claim" (the rule grounds it at 6)
MATCH_M = 2500.0          # a radar echo this close to the stopped AIS marker is taken to be the same ship
RUNNER_SPEED = 7.0        # m/s used in the Runner plan
DARK_LEAD_M = 62000.0     # about 30 ticks of sailing before the zone (the long-dark plan the learned Runner found, see docs/RULES_V3.md)
DARK_TAIL_TICKS = 8       # stay dark this long after the transfer is complete
STOP_WITHIN_M = 2500.0    # stop once this close to the zone centre (the zone radius is 3 km; one tick of sailing is ~2.1 km)


def _d(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)


def _bearing(ax: float, ay: float, bx: float, by: float) -> float:
    return math.degrees(math.atan2(bx - ax, by - ay)) % 360.0


class HunterCoach:
    def __init__(self) -> None:
        self.mmsi: str | None = None
        self.first_seen: dict[str, tuple[float, float]] = {}
        self.reports: dict[int, dict] = {}   # report time -> the target's AIS contact; the observation holds only the latest report per ship

    def _pick_target(self, obs: dict) -> None:
        """The target's own AIS report can arrive a tick or two late, so wait until tick 2 and take the ship whose
        first report was nearest the last-known position."""
        lk = obs.get("brief", {}).get("last_known")
        for c in obs["contacts"]:
            if c["source"] == "ais":
                self.first_seen.setdefault(c["claimed_mmsi"], (c["x_m"], c["y_m"]))
        if lk and obs["tick"] >= 2 and self.first_seen:
            self.mmsi = min(self.first_seen, key=lambda m: _d(*self.first_seen[m], lk["x_m"], lk["y_m"]))

    def update(self, obs: dict) -> dict:
        tick = obs["tick"]
        if obs["status"]["outcome"] != "none":
            return {"step": 7, "of": 7, "title": "Game over", "text": "Read the debrief below the event log: it shows where the transfer really happened (yellow circle) and the Runner's true track (pink)."}
        if self.mmsi is None:
            self._pick_target(obs)
            if self.mmsi is None:
                return {"step": 1, "of": 7, "title": "1. Find your target", "text": "You are hunting one large tanker (a VLCC), last seen inside the grey circle. Press +1 a couple of times so its AIS reports come in."}
        for c in obs["contacts"]:
            if c["source"] == "ais" and c["claimed_mmsi"] == self.mmsi:
                self.reports[tick - c["age_ticks"]] = {**c, "age_ticks": c["age_ticks"]}
        mine = [{**c, "age_ticks": tick - t} for t, c in sorted(self.reports.items())]
        if not mine:
            return {"step": 1, "of": 7, "title": "No target in sight", "text": "No AIS report from the likely target yet. Press +1 and wait."}
        last = mine[-1]
        label = {last["contact_id"]: "your target (AIS)"}
        area = obs["brief"].get("transfer_area")
        in_area = bool(area) and _d(last["x_m"], last["y_m"], area["x_m"], area["y_m"]) <= area["radius_m"]
        # walk back through the reports while the marker has not moved: that run is "stopped"
        run = [last]
        for c in reversed(mine[:-1]):
            if _d(c["x_m"], c["y_m"], run[0]["x_m"], run[0]["y_m"]) < STOP_M:
                run.insert(0, c)
            else:
                break
        stopped = len(run) >= 2
        t_stop = tick - run[0]["age_ticks"] if stopped else None
        stopped_ticks = tick - t_stop if stopped else 0
        if len(mine) < 2:
            return {"step": 1, "of": 7, "title": "1. Find your target", "highlight": label,
                    "text": "You are hunting one large tanker (a VLCC) that was last seen inside the grey circle. The highlighted blue triangle is the AIS marker nearest that circle: your likely target. "
                            "The dashed yellow circle is where it is expected to stop and meet another ship. Press +6 to follow it."}
        if not stopped:
            km = "" if not area else f" It is {_d(last['x_m'], last['y_m'], area['x_m'], area['y_m']) / 1000:.0f} km from the centre of the dashed circle."
            return {"step": 2, "of": 7, "title": "2. Follow it until it stops", "highlight": label,
                    "text": "The target is still sailing." + km + " Press +6 (or Auto) and watch its blue trail. You are waiting for the marker to stop moving inside the dashed circle: "
                            "a tanker sitting still in the middle of the ocean is the transfer. Do not spend radar passes yet; you only have a few."}
        if not in_area:
            return {"step": 2, "of": 7, "title": "2. It stopped, but not here", "highlight": label,
                    "text": "The target has stopped outside the dashed circle. That is not the expected transfer; keep following it with +6."}
        sar = [c for c in obs["contacts"] if c["source"] == "sar" and c.get("size_class") == "large" and tick - c["age_ticks"] >= t_stop
               and _d(c["x_m"], c["y_m"], last["x_m"], last["y_m"]) <= MATCH_M]
        pending = [p for p in obs["resources"]["pending_taskings"] if _d(p["x_m"], p["y_m"], last["x_m"], last["y_m"]) <= obs["resources"]["sar_footprint_radius_m"]]
        if not sar:
            if pending:
                return {"step": 4, "of": 7, "title": "4. Radar pass on the way", "highlight": label,
                        "text": f"A radar pass is booked and reports at tick {pending[0]['exec_tick']}. Press +1 until the orange diamond appears."}
            if obs["resources"]["sar_tasks_remaining"] < 1:
                return {"step": 4, "of": 7, "title": "Out of radar passes", "highlight": label, "text": "No passes left; you cannot back a claim with radar."}
            return {"step": 3, "of": 7, "title": "3. It has stopped: look with radar", "highlight": label,
                    "text": f"The target stopped {stopped_ticks} tick(s) ago inside the dashed circle. Radar sees through a switched-off AIS, so you need a radar picture of that spot as proof. "
                            "Press the button to spend one of your radar passes on it. The result arrives 3 ticks later.",
                    "button": {"label": "Task a radar pass on the target", "kind": "task_sar", "x_m": last["x_m"], "y_m": last["y_m"]}}
        echo = min(sar, key=lambda c: (_d(c["x_m"], c["y_m"], last["x_m"], last["y_m"]), c["age_ticks"]))
        label[echo["contact_id"]] = "radar echo of the same ship"
        others = [c for c in obs["contacts"] if c["source"] == "sar" and c.get("size_class") == "large" and c["contact_id"] != echo["contact_id"]
                  and tick - c["age_ticks"] >= t_stop and _d(c["x_m"], c["y_m"], last["x_m"], last["y_m"]) <= 15000.0]
        decoy = f" A second large echo {_d(others[0]['x_m'], others[0]['y_m'], last['x_m'], last['y_m']) / 1000:.0f} km away is some other ship (a decoy): it has no AIS match, ignore it." if others else ""
        if stopped_ticks < READY_TICKS:
            return {"step": 5, "of": 7, "title": "5. Radar agrees: now wait", "highlight": label,
                    "text": f"The orange diamond sits exactly on the stopped AIS marker, so radar and AIS agree it is the same ship." + decoy +
                            f" A claim only counts once the ship has stopped for 6 ticks (so that it cannot be a passing ship); it has been {stopped_ticks}. Press +1 until it has been {READY_TICKS}. Claiming earlier costs 25 points."}
        return {"step": 6, "of": 7, "title": "6. File the claim", "highlight": label,
                "text": "The ship has been stopped inside the dashed circle for long enough, and two independent sensors agree. Press the first button to select the AIS marker and the radar echo as evidence, "
                        "then the red File claim button. A correct claim ends the game with a high score." + decoy,
                "button": {"label": "Select the evidence for me", "kind": "select", "selected": last["contact_id"], "evidence": [last["contact_id"], echo["contact_id"]]}}


class RunnerCoach:
    def __init__(self) -> None:
        self.done_tick: int | None = None

    def update(self, obs: dict) -> dict:
        tick = obs["tick"]
        if obs["status"]["outcome"] != "none":
            return {"step": 6, "of": 6, "title": "Game over", "text": "Read the debrief below the event log."}
        own, tr, dl = obs["own"], obs["mission"]["transfer"], obs["mission"]["delivery"]
        d_zone = _d(own["x_m"], own["y_m"], tr["x_m"], tr["y_m"])
        done = tr["progress_ticks"] >= tr["required_ticks"]
        if done and self.done_tick is None:
            self.done_tick = tick
        want_ais = "on"
        if done:
            course, speed = _bearing(own["x_m"], own["y_m"], dl["x_m"], dl["y_m"]), 8.0
            if tick - self.done_tick < DARK_TAIL_TICKS:
                want_ais = "dark"
            step, title = 5, "5. Transfer done: run for the delivery circle"
            text = ("The transfer is complete. Head for the green circle at high speed. Stay dark a few more ticks so nobody can tie the stop to your ship, then switch AIS back on." if want_ais == "dark"
                    else "AIS can go back on now. Keep going to the green circle.")
        elif d_zone <= STOP_WITHIN_M or tr["progress_ticks"] > 0:
            course, speed = own["course_deg"], 0.0
            want_ais = "dark"
            step, title = 4, f"4. Loiter in the circle ({tr['progress_ticks']}/{tr['required_ticks']})"
            text = "You are inside the yellow circle with speed 0. Each tick here counts. Press +6 until the counter reaches 12. Stay dark: a stopped ship with AIS on is the easiest thing for the Hunter to find."
        else:
            course, speed = _bearing(own["x_m"], own["y_m"], tr["x_m"], tr["y_m"]), RUNNER_SPEED
            if d_zone <= DARK_LEAD_M:
                want_ais = "dark"
                step, title = 3, "3. Switch AIS off, keep sailing"
                text = (f"You are {d_zone / 1000:.0f} km from the yellow circle. The Hunter tracks your AIS and guesses where you will be when it goes silent. "
                        "Going dark early and for long makes your position uncertain by more than a radar pass can cover, so it has to guess. The cost: you are invisible but also unaccounted for.")
            else:
                step, title = 1, "1. Sail to the yellow circle"
                text = (f"You are {d_zone / 1000:.0f} km from the yellow circle where the transfer must happen. Keep AIS on for now (a ship that is quiet all the way looks odd). "
                        f"Sail straight at it. Go dark once you are within {DARK_LEAD_M / 1000:.0f} km.")
        orders = []
        if abs(((own["course_deg"] - course + 180) % 360) - 180) > 3.0 or abs(own["speed_mps"] - speed) > 0.3:
            orders.append({"action_type": "set_course_speed", "params": {"course_deg": round(course, 1), "speed_mps": speed}})
        if own["ais_mode"] != want_ais:
            orders.append({"action_type": "set_ais_mode", "params": {"mode": want_ais}})
        out = {"step": step, "of": 5, "title": title, "text": text}
        if orders:
            desc = ", ".join((f"course {o['params']['course_deg']:.0f}°, speed {o['params']['speed_mps']:.1f}" if o["action_type"] == "set_course_speed" else f"AIS {o['params']['mode']}") for o in orders)
            out["button"] = {"label": f"Apply: {desc}", "kind": "orders", "orders": orders}
        else:
            out["text"] += " Orders are correct: press +6."
        return out
