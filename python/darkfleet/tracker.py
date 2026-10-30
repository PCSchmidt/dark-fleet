"""Hunter-side estimators of the target vessel's position.

Both trackers consume only the Hunter's role-filtered observation documents.

* ``DeadReckoning``  baseline: extrapolates the last AIS fixes of the target; ignores SAR.
* ``ParticleFilter`` Bayesian filter with a maneuvering motion model, a robust AIS likelihood
                     and a SAR likelihood that also uses negative information (a pass that
                     covers a region and does not see the target lowers the weight there).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


def _target_fixes(obs: dict) -> list[tuple[int, float, float]]:
    """(fix_tick, x, y) for every visible AIS contact of the briefed target."""
    brief = obs.get("brief")
    if not brief:
        return []
    out = []
    for c in obs["contacts"]:
        if c["source"] == "ais" and c["claimed_mmsi"] == brief["target_mmsi"]:
            out.append((obs["tick"] - c["age_ticks"], c["x_m"], c["y_m"]))
    return sorted(out)


def _sar_passes(obs: dict) -> dict[int, list[tuple[float, float, float]]]:
    """exec_tick -> list of (x, y, sigma) for SAR contacts, keyed by the tick they were produced."""
    passes: dict[int, list[tuple[float, float, float]]] = {}
    for c in obs["contacts"]:
        if c["source"] == "sar":
            passes.setdefault(obs["tick"] - c["age_ticks"], []).append((c["x_m"], c["y_m"], c["sigma_m"]))
    return passes


class DeadReckoning:
    """Extrapolate the target from its last AIS fixes at constant velocity."""

    name = "dead_reckoning"

    def __init__(self, tick_s: float):
        self.tick_s = tick_s
        self.fixes: dict[int, tuple[float, float]] = {}
        self.tick = 0

    def update(self, obs: dict) -> None:
        self.tick = obs["tick"]
        for t, x, y in _target_fixes(obs):
            self.fixes[t] = (x, y)

    def estimate(self) -> tuple[float, float] | None:
        if not self.fixes:
            return None
        ticks = sorted(self.fixes)
        t1 = ticks[-1]
        x1, y1 = self.fixes[t1]
        older = [t for t in ticks if 3 <= t1 - t <= 8]
        if not older:
            older = [t for t in ticks if t < t1]
        if not older:
            return (x1, y1)
        t0 = older[0] if len(older) == 1 else older[0]
        x0, y0 = self.fixes[t0]
        vx, vy = (x1 - x0) / ((t1 - t0) * self.tick_s), (y1 - y0) / ((t1 - t0) * self.tick_s)
        dt = (self.tick - t1) * self.tick_s
        return (x1 + vx * dt, y1 + vy * dt)

    def last_fix_tick(self) -> int | None:
        return max(self.fixes) if self.fixes else None


@dataclass
class PFConfig:
    n_particles: int = 3000
    sigma_v: float = 0.08          # speed random walk per tick (m/s)
    sigma_course: float = 0.03     # small heading noise per tick (rad)
    p_jump: float = 0.02           # per-tick probability of an evasive heading change
    sigma_jump: float = 0.9        # std of the heading change (rad)
    v_max: float = 8.5
    ais_epsilon: float = 0.05      # outlier mass for AIS fixes (robustness to spoofed positions)
    p_detect: float = 0.95         # SAR detection probability for a large target
    ess_fraction: float = 0.5
    seed: int = 0


class ParticleFilter:
    """Bootstrap particle filter over (x, y, speed, course)."""

    name = "particle_filter"

    def __init__(self, tick_s: float, config: PFConfig | None = None):
        self.cfg = config or PFConfig()
        self.tick_s = tick_s
        self.rng = np.random.default_rng(self.cfg.seed)
        self.tick = 0
        self.initialized = False
        self.processed_fix_ticks: set[int] = set()
        self.processed_passes: set[int] = set()
        n = self.cfg.n_particles
        self.x = np.zeros(n)
        self.y = np.zeros(n)
        self.v = np.zeros(n)
        self.c = np.zeros(n)
        self.w = np.full(n, 1.0 / n)
        self.sar_radius = 40000.0
        self.pending: dict[int, tuple[float, float]] = {}
        self.sigma_ais = 30.0
        self.fix_log: list[tuple[int, float, float]] = []

    # -- lifecycle ----------------------------------------------------------------
    def update(self, obs: dict) -> None:
        t = obs["tick"]
        if "resources" in obs:
            self.sar_radius = obs["resources"]["sar_footprint_radius_m"]
            for p in obs["resources"]["pending_taskings"]:
                self.pending[p["exec_tick"]] = (p["x_m"], p["y_m"])
        seen = {f[0] for f in self.fix_log}
        for f in _target_fixes(obs):
            if f[0] not in seen:
                self.fix_log.append(f)
        self.fix_log.sort()
        if not self.initialized:
            # Wait for a short track so the velocity prior is informative (a broad velocity
            # prior would leave almost no particles consistent with 30 m fixes).
            if len(self.fix_log) < 4 or self.fix_log[-1][0] - self.fix_log[0][0] < 3:
                self.tick = t
                return
            self._init_from_track(self.fix_log)
            self.tick = self.fix_log[-1][0]
            self.processed_fix_ticks.update(f[0] for f in self.fix_log)
        fixes = [f for f in self.fix_log if f[0] not in self.processed_fix_ticks]
        # Advance to the current tick, processing evidence in time order.
        events: dict[int, list] = {}
        for ft, x, y in fixes:
            events.setdefault(ft, []).append(("ais", x, y))
        passes = _sar_passes(obs)
        for et, contacts in passes.items():
            if et not in self.processed_passes:
                events.setdefault(et, []).append(("sar", et, contacts))
        # SAR passes with zero contacts are visible only via the pending order list.
        for et, (cx, cy) in list(self.pending.items()):
            if et <= t and et not in self.processed_passes and et not in passes:
                events.setdefault(et, []).append(("sar", et, []))
        for et in sorted(events):
            if et > self.tick:
                self._predict(et - self.tick)
                self.tick = et
            for ev in events[et]:
                if ev[0] == "ais":
                    self._update_ais(ev[1], ev[2])
                    self.processed_fix_ticks.add(et)
                else:
                    self._update_sar(et, ev[2])
                    self.processed_passes.add(et)
        if t > self.tick:
            self._predict(t - self.tick)
            self.tick = t

    def _init_from_track(self, log: list[tuple[int, float, float]]) -> None:
        """Initialise around the latest fix with velocity from a least-squares line fit."""
        t = np.array([f[0] for f in log], dtype=float)
        xs = np.array([f[1] for f in log])
        ys = np.array([f[2] for f in log])
        tt = (t - t[-1]) * self.tick_s
        vx = float(np.polyfit(tt, xs, 1)[0])
        vy = float(np.polyfit(tt, ys, 1)[0])
        speed = math.hypot(vx, vy)
        course = math.atan2(vx, vy)
        n = self.cfg.n_particles
        self.x = xs[-1] + self.rng.normal(0, self.sigma_ais, n)
        self.y = ys[-1] + self.rng.normal(0, self.sigma_ais, n)
        self.v = np.clip(speed + self.rng.normal(0, 0.15, n), 0.5, self.cfg.v_max)
        self.c = course + self.rng.normal(0, 0.03, n)
        self.w = np.full(n, 1.0 / n)
        self.initialized = True

    # -- model ----------------------------------------------------------------------
    def _predict(self, n_ticks: int) -> None:
        cfg = self.cfg
        n = cfg.n_particles
        for _ in range(n_ticks):
            self.v = np.clip(self.v + self.rng.normal(0, cfg.sigma_v, n), 0.5, cfg.v_max)
            jump = self.rng.random(n) < cfg.p_jump
            self.c = self.c + self.rng.normal(0, cfg.sigma_course, n) + jump * self.rng.normal(0, cfg.sigma_jump, n)
            self.x = self.x + self.v * self.tick_s * np.sin(self.c)
            self.y = self.y + self.v * self.tick_s * np.cos(self.c)

    def _normalize(self) -> None:
        s = self.w.sum()
        if not np.isfinite(s) or s < 1e-300:
            # Evidence inconsistent with every particle: widen and reset rather than collapse.
            n = self.cfg.n_particles
            self.x = self.x + self.rng.normal(0, 5000.0, n)
            self.y = self.y + self.rng.normal(0, 5000.0, n)
            self.w = np.full(n, 1.0 / n)
            return
        self.w = self.w / s
        ess = 1.0 / np.sum(self.w**2)
        if ess < self.cfg.ess_fraction * self.cfg.n_particles:
            self._resample()

    def _resample(self) -> None:
        n = self.cfg.n_particles
        positions = (self.rng.random() + np.arange(n)) / n
        idx = np.searchsorted(np.cumsum(self.w), positions)
        idx = np.minimum(idx, n - 1)
        self.x, self.y, self.v, self.c = self.x[idx], self.y[idx], self.v[idx], self.c[idx]
        self.w = np.full(n, 1.0 / n)

    # -- evidence -------------------------------------------------------------------
    def _update_ais(self, zx: float, zy: float) -> None:
        sig = self.sigma_ais
        d2 = (self.x - zx) ** 2 + (self.y - zy) ** 2
        gauss = np.exp(-0.5 * d2 / sig**2) / (2 * math.pi * sig**2)
        clutter = 1.0 / (2 * 250000.0) ** 2  # uniform over the region
        self.w = self.w * ((1 - self.cfg.ais_epsilon) * gauss + self.cfg.ais_epsilon * clutter)
        self._normalize()

    def _update_sar(self, exec_tick: int, contacts: list[tuple[float, float, float]]) -> None:
        """SAR pass update.

        Hypotheses: (miss) the target was inside the footprint and not detected, or was outside it;
        (k) contact k is the target. Contact hypotheses are handled with a measurement-driven
        proposal: new particles are born at each contact, with mass proportional to the smoothed
        prior density at that contact (plus the usual clutter normalisation). A direct per-particle
        likelihood would leave only a handful of particles within a few sigma of a contact and make
        the posterior depend on which particles happened to land there.
        """
        center = self.pending.get(exec_tick)
        if center is None:
            return
        cfg = self.cfg
        n = cfg.n_particles
        cx, cy = center
        r = self.sar_radius
        inside = (self.x - cx) ** 2 + (self.y - cy) ** 2 <= r * r
        pd = cfg.p_detect
        if not contacts:
            self.w = self.w * np.where(inside, 1 - pd, 1.0)
            self._normalize()
            return

        kappa = len(contacts) / (math.pi * r * r)
        sig = contacts[0][2]
        h = max(3000.0, 2.0 * self.spread_m() / 3.0)  # smoothing bandwidth for the prior density

        miss_w = self.w * np.where(inside, 1 - pd, 1.0)
        miss_mass = float(miss_w.sum())
        births = []
        for zx, zy, _ in contacts:
            kern = np.exp(-0.5 * ((self.x - zx) ** 2 + (self.y - zy) ** 2) / h**2) / (2 * math.pi * h**2)
            q = float(np.sum(self.w * kern))
            births.append((zx, zy, pd * q / kappa, kern * self.w))
        total = miss_mass + sum(b[2] for b in births)
        if total < 1e-300:
            self._normalize()
            return

        new_x, new_y, new_v, new_c = [], [], [], []
        # surviving (missed / outside) particles, resampled to their share
        n_miss = int(round(n * miss_mass / total))
        if n_miss > 0 and miss_mass > 0:
            idx = self.rng.choice(n, size=n_miss, p=miss_w / miss_mass)
            new_x.append(self.x[idx]); new_y.append(self.y[idx]); new_v.append(self.v[idx]); new_c.append(self.c[idx])
        for zx, zy, mass, kw in births:
            m = int(round(n * mass / total))
            if m <= 0 or kw.sum() <= 0:
                continue
            donor = self.rng.choice(n, size=m, p=kw / kw.sum())  # inherit plausible speed/course
            new_x.append(zx + self.rng.normal(0, sig, m)); new_y.append(zy + self.rng.normal(0, sig, m))
            new_v.append(self.v[donor]); new_c.append(self.c[donor])
        if not new_x:
            self._normalize()
            return
        x = np.concatenate(new_x); y = np.concatenate(new_y); v = np.concatenate(new_v); c = np.concatenate(new_c)
        if len(x) < n:  # rounding shortfall: pad by resampling the new set
            pad = self.rng.integers(0, len(x), n - len(x))
            x = np.concatenate([x, x[pad]]); y = np.concatenate([y, y[pad]]); v = np.concatenate([v, v[pad]]); c = np.concatenate([c, c[pad]])
        self.x, self.y, self.v, self.c = x[:n], y[:n], v[:n], c[:n]
        self.w = np.full(n, 1.0 / n)

    # -- outputs --------------------------------------------------------------------
    def posterior_mean(self) -> tuple[float, float] | None:
        if not self.initialized:
            return None
        return (float(np.sum(self.w * self.x)), float(np.sum(self.w * self.y)))

    def estimate(self) -> tuple[float, float] | None:
        """Position of the dominant posterior mode (mean of particles around the densest point).

        The posterior is often multimodal after a SAR pass (several plausible contacts); the plain
        mean can fall between modes where nothing is. ``posterior_mean`` is kept for reference.
        """
        if not self.initialized:
            return None
        k = min(400, self.cfg.n_particles)
        idx = self.rng.choice(self.cfg.n_particles, size=k, replace=False, p=self.w / self.w.sum()) if False else np.arange(0, self.cfg.n_particles, max(1, self.cfg.n_particles // k))[:k]
        bw = 5000.0
        d2 = (self.x[idx][:, None] - self.x[None, :]) ** 2 + (self.y[idx][:, None] - self.y[None, :]) ** 2
        dens = (np.exp(-0.5 * d2 / bw**2) * self.w[None, :]).sum(axis=1)
        best = idx[int(np.argmax(dens))]
        near = (self.x - self.x[best]) ** 2 + (self.y - self.y[best]) ** 2 <= (2 * bw) ** 2
        ws = self.w[near]
        if ws.sum() <= 0:
            return self.posterior_mean()
        return (float(np.sum(ws * self.x[near]) / ws.sum()), float(np.sum(ws * self.y[near]) / ws.sum()))

    def spread_m(self) -> float:
        if not self.initialized:
            return float("inf")
        mx, my = self.posterior_mean()
        var = np.sum(self.w * ((self.x - mx) ** 2 + (self.y - my) ** 2))
        return float(math.sqrt(var))

    def mass_within(self, x: float, y: float, radius_m: float) -> float:
        """Posterior mass within ``radius_m`` of a point (evaluation helper)."""
        if not self.initialized:
            return 0.0
        return float(np.sum(self.w[(self.x - x) ** 2 + (self.y - y) ** 2 <= radius_m**2]))

    def log_density_at(self, x: float, y: float, bandwidth_m: float = 3000.0) -> float:
        """log of a Gaussian-kernel posterior density at a point (for NLL evaluation)."""
        if not self.initialized:
            return float("-inf")
        d2 = (self.x - x) ** 2 + (self.y - y) ** 2
        dens = np.sum(self.w * np.exp(-0.5 * d2 / bandwidth_m**2)) / (2 * math.pi * bandwidth_m**2)
        return float(math.log(max(dens, 1e-300)))
