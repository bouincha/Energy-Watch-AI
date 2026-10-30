"""Energy dataset: a realistic, deterministic simulation of 4 sites with hourly sub-metering.

The simulation contains five injected anomalies (the "ground truth") so the anomaly
detector can be evaluated. Replace ``EnergyDataset`` with a connector to a real
metering platform (MQTT, OPC-UA, SCADA export, utility API...) for production use.

Tariffs and the grid emission factor are illustrative values for the demo.
"""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache

# --------------------------------------------------------------------------- reference data

TARIFF_MAD_PER_KWH = {"peak": 1.45, "standard": 1.05, "off_peak": 0.75}  # illustrative
GRID_CO2_KG_PER_KWH = 0.72  # illustrative grid emission factor


def tariff_period(hour: int) -> str:
    if 18 <= hour < 22:
        return "peak"
    if 7 <= hour < 18:
        return "standard"
    return "off_peak"


@dataclass(frozen=True)
class Meter:
    id: str
    name: str
    category: str  # hvac | lighting | it | process | cooling | medical | solar ...
    generation: bool = False


@dataclass(frozen=True)
class Site:
    id: str
    name: str
    city: str
    type: str
    area_m2: int
    meters: tuple[Meter, ...]
    business_hours: tuple[int, int]
    working_days: tuple[int, ...]  # 0 = Monday


SITES: tuple[Site, ...] = (
    Site("HQ-CASA", "Casablanca Headquarters", "Casablanca", "office", 12_000, (
        Meter("HQ-CASA-HVAC", "HVAC (chillers + AHU)", "hvac"),
        Meter("HQ-CASA-LGT", "Lighting", "lighting"),
        Meter("HQ-CASA-IT", "IT & plug loads", "it"),
        Meter("HQ-CASA-ELV", "Elevators & services", "services"),
    ), (8, 18), (0, 1, 2, 3, 4)),
    Site("PLANT-KEN", "Kenitra Assembly Plant", "Kenitra", "industrial", 38_000, (
        Meter("PLANT-KEN-AIR", "Compressed air", "compressed_air"),
        Meter("PLANT-KEN-LINE", "Production lines", "process"),
        Meter("PLANT-KEN-HVAC", "HVAC", "hvac"),
        Meter("PLANT-KEN-LGT", "Lighting", "lighting"),
        Meter("PLANT-KEN-PV", "Rooftop solar PV", "solar", generation=True),
    ), (6, 22), (0, 1, 2, 3, 4, 5)),
    Site("DC-RABAT", "Rabat Data Center", "Rabat", "data_center", 4_500, (
        Meter("DC-RABAT-IT", "IT load (servers)", "it"),
        Meter("DC-RABAT-COOL", "Cooling (CRAH + chillers)", "cooling"),
        Meter("DC-RABAT-UPS", "UPS & distribution losses", "losses"),
    ), (0, 24), (0, 1, 2, 3, 4, 5, 6)),
    Site("CLINIC-MRK", "Marrakech Clinic", "Marrakech", "healthcare", 9_000, (
        Meter("CLINIC-MRK-HVAC", "HVAC", "hvac"),
        Meter("CLINIC-MRK-MED", "Medical equipment", "medical"),
        Meter("CLINIC-MRK-LGT", "Lighting", "lighting"),
        Meter("CLINIC-MRK-DHW", "Hot water", "water_heating"),
    ), (7, 20), (0, 1, 2, 3, 4, 5, 6)),
)

SITE_BY_ID = {s.id: s for s in SITES}
METER_BY_ID = {m.id: (s, m) for s in SITES for m in s.meters}


@dataclass
class InjectedAnomaly:
    meter_id: str
    kind: str
    description: str
    start_day: int  # index in the dataset (0 = oldest day)
    end_day: int


@dataclass
class AnomalyEvent:
    id: str
    site_id: str
    site_name: str
    meter_id: str
    meter_name: str
    category: str
    pattern: str  # night_operation | weekend_operation | sustained_increase | spike | drift
    start: str
    end: str
    hours: int
    excess_kwh: float
    excess_cost_mad: float
    peak_deviation_pct: float
    max_zscore: float
    severity: str
    description: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class EnergyDataset:
    start: datetime
    days: int
    # meter_id -> list of hourly kWh (len = days*24)
    series: dict[str, list[float]] = field(default_factory=dict)
    injected: list[InjectedAnomaly] = field(default_factory=list)
    _anomaly_cache: list[AnomalyEvent] | None = None
    _baseline_cache: dict[str, dict[tuple[bool, int], float]] = field(default_factory=dict)

    # ------------------------------------------------------------------ helpers
    @property
    def hours(self) -> int:
        return self.days * 24

    @property
    def end(self) -> datetime:
        return self.start + timedelta(hours=self.hours)

    def ts(self, i: int) -> datetime:
        return self.start + timedelta(hours=i)

    def _site_meters(self, site_id: str | None, include_generation: bool = False) -> list[tuple[Site, Meter]]:
        out = []
        for s in SITES:
            if site_id and s.id != site_id:
                continue
            for m in s.meters:
                if m.generation and not include_generation:
                    continue
                out.append((s, m))
        return out

    def _window(self, days: int) -> range:
        days = max(1, min(days, self.days))
        return range(self.hours - days * 24, self.hours)

    @staticmethod
    def _cost(kwh: float, hour: int) -> float:
        return kwh * TARIFF_MAD_PER_KWH[tariff_period(hour)]

    # ------------------------------------------------------------------ queries
    def sites(self) -> list[dict]:
        out = []
        for s in SITES:
            summ = self.summary(s.id, days=7)
            out.append({
                "id": s.id, "name": s.name, "city": s.city, "type": s.type, "area_m2": s.area_m2,
                "meters": [{"id": m.id, "name": m.name, "category": m.category, "generation": m.generation}
                           for m in s.meters],
                "last_7d_kwh": summ["consumption_kwh"], "last_7d_cost_mad": summ["cost_mad"],
                "intensity_kwh_m2_7d": round(summ["consumption_kwh"] / s.area_m2, 3),
                "open_anomalies": sum(1 for a in self.anomalies() if a.site_id == s.id),
            })
        return out

    def summary(self, site_id: str | None = None, days: int = 30) -> dict:
        window = self._window(days)
        days = len(window) // 24
        cons = cost = 0.0
        by_meter: dict[str, float] = defaultdict(float)
        by_category: dict[str, float] = defaultdict(float)
        by_period: dict[str, float] = defaultdict(float)
        hourly_total = [0.0] * len(window)
        for _, m in self._site_meters(site_id):
            values = self.series[m.id]
            for j, i in enumerate(window):
                v = values[i]
                hour = self.ts(i).hour
                cons += v
                cost += self._cost(v, hour)
                by_meter[m.id] += v
                by_category[m.category] += v
                by_period[tariff_period(hour)] += v
                hourly_total[j] += v
        solar = sum(self.series[m.id][i] for _, m in self._site_meters(site_id, True) if m.generation
                    for i in window)
        net = max(cons - solar, 0.0)
        prev = self._window(days * 2)
        prev_cons = 0.0
        if len(prev) == 2 * len(window):
            prev_range = range(prev.start, window.start)
            prev_cons = sum(self.series[m.id][i] for _, m in self._site_meters(site_id) for i in prev_range)
        change = ((cons - prev_cons) / prev_cons * 100) if prev_cons else None
        peak_idx = max(range(len(hourly_total)), key=hourly_total.__getitem__) if hourly_total else 0
        return {
            "site_id": site_id or "ALL",
            "days": days,
            "period_start": self.ts(window.start).isoformat(),
            "period_end": self.end.isoformat(),
            "consumption_kwh": round(cons, 1),
            "solar_generation_kwh": round(solar, 1),
            "net_grid_kwh": round(net, 1),
            "renewable_share_pct": round(solar / cons * 100, 1) if cons else 0.0,
            "cost_mad": round(cost - solar * TARIFF_MAD_PER_KWH["standard"], 1),
            "co2_tonnes": round(net * GRID_CO2_KG_PER_KWH / 1000, 2),
            "peak_demand_kw": round(max(hourly_total) if hourly_total else 0, 1),
            "peak_at": self.ts(window.start + peak_idx).isoformat(),
            "change_vs_previous_pct": round(change, 1) if change is not None else None,
            "by_meter": {k: round(v, 1) for k, v in sorted(by_meter.items(), key=lambda kv: -kv[1])},
            "by_category": {k: round(v, 1) for k, v in sorted(by_category.items(), key=lambda kv: -kv[1])},
            "by_tariff_period": {k: round(v, 1) for k, v in by_period.items()},
        }

    def daily(self, site_id: str | None = None, days: int = 30, by: str = "site") -> dict:
        """Daily kWh grouped by site or by meter category, for charts."""
        window = self._window(days)
        n_days = len(window) // 24
        labels = [(self.ts(window.start) + timedelta(days=d)).date().isoformat() for d in range(n_days)]
        groups: dict[str, list[float]] = defaultdict(lambda: [0.0] * n_days)
        for s, m in self._site_meters(site_id, include_generation=True):
            key = s.id if by == "site" else m.category
            if m.generation:
                key = "solar_generation"
            for j, i in enumerate(window):
                groups[key][j // 24] += self.series[m.id][i]
        return {"labels": labels, "series": {k: [round(x, 1) for x in v] for k, v in groups.items()}}

    def hourly(self, site_id: str | None = None, hours: int = 72) -> dict:
        hours = max(1, min(hours, self.hours))
        rng = range(self.hours - hours, self.hours)
        total = [0.0] * hours
        solar = [0.0] * hours
        for _, m in self._site_meters(site_id, include_generation=True):
            target = solar if m.generation else total
            for j, i in enumerate(rng):
                target[j] += self.series[m.id][i]
        return {"labels": [self.ts(i).isoformat() for i in rng],
                "consumption_kw": [round(x, 1) for x in total], "solar_kw": [round(x, 1) for x in solar]}

    def meter_profile(self, meter_id: str, days: int = 7) -> dict:
        site, meter = METER_BY_ID[meter_id]
        rng = self._window(days)
        base = self._baseline(meter_id)
        return {
            "meter_id": meter_id, "meter_name": meter.name, "site_id": site.id,
            "labels": [self.ts(i).isoformat() for i in rng],
            "actual_kwh": [round(self.series[meter_id][i], 2) for i in rng],
            "baseline_kwh": [round(base[self._slot(i, site)], 2) for i in rng],
        }

    def compare_periods(self, site_id: str | None = None, days: int = 7) -> dict:
        cur = self.summary(site_id, days)
        window = self._window(days)
        prev_range = range(window.start - len(window), window.start)
        prev_by_cat: dict[str, float] = defaultdict(float)
        for _, m in self._site_meters(site_id):
            prev_by_cat[m.category] += sum(self.series[m.id][i] for i in prev_range)
        deltas = {}
        for cat, v in cur["by_category"].items():
            p = prev_by_cat.get(cat, 0.0)
            deltas[cat] = {"current_kwh": v, "previous_kwh": round(p, 1),
                           "change_pct": round((v - p) / p * 100, 1) if p else None}
        return {"site_id": site_id or "ALL", "days": days, "total_change_pct": cur["change_vs_previous_pct"],
                "by_category": dict(sorted(deltas.items(), key=lambda kv: -(kv[1]["change_pct"] or 0)))}

    def data_center_pue(self, days: int = 7) -> dict:
        series = []
        for d in range(self.days - days, self.days):
            rng = range(d * 24, d * 24 + 24)
            it = sum(self.series["DC-RABAT-IT"][i] for i in rng)
            total = it + sum(self.series[m][i] for m in ("DC-RABAT-COOL", "DC-RABAT-UPS") for i in rng)
            series.append(round(total / it, 3))
        return {"site_id": "DC-RABAT", "daily_pue": series, "current_pue": series[-1],
                "target_pue": 1.45, "baseline_pue": series[0]}

    # ------------------------------------------------------------------ anomaly detection
    TRAINING_DAYS = 14

    def _slot(self, i: int, site: Site) -> tuple[bool, int]:
        """Baseline bucket: (is a working day for this site, hour of day)."""
        t = self.ts(i)
        return (t.weekday() in site.working_days, t.hour)

    def _baseline(self, meter_id: str) -> dict[tuple[bool, int], float]:
        if meter_id in self._baseline_cache:
            return self._baseline_cache[meter_id]
        site = METER_BY_ID[meter_id][0]
        buckets: dict[tuple[bool, int], list[float]] = defaultdict(list)
        for i in range(self.TRAINING_DAYS * 24):
            buckets[self._slot(i, site)].append(self.series[meter_id][i])
        base = {k: statistics.median(v) for k, v in buckets.items()}
        self._baseline_cache[meter_id] = base
        return base

    def anomalies(self, site_id: str | None = None) -> list[AnomalyEvent]:
        if self._anomaly_cache is None:
            self._anomaly_cache = self._detect_all()
        return [a for a in self._anomaly_cache if site_id is None or a.site_id == site_id]

    def _detect_all(self) -> list[AnomalyEvent]:
        events: list[AnomalyEvent] = []
        for site, meter in self._site_meters(None):
            events.extend(self._detect_meter(site, meter))
        events.sort(key=lambda e: (-e.excess_cost_mad))
        for n, e in enumerate(events, 1):
            e.id = f"ANOM-{n:03d}"
        return events

    def _detect_meter(self, site: Site, meter: Meter) -> list[AnomalyEvent]:
        values = self.series[meter.id]
        base = self._baseline(meter.id)
        train = range(self.TRAINING_DAYS * 24)
        residuals = [values[i] - base[self._slot(i, site)] for i in train]
        med = statistics.median(residuals)
        mad = statistics.median([abs(r - med) for r in residuals]) * 1.4826
        mean_base = statistics.fmean(base.values())
        scale = max(mad, 0.04 * mean_base, 0.5)

        flagged: list[tuple[int, float, float]] = []
        for i in range(self.TRAINING_DAYS * 24, self.hours):
            b = base[self._slot(i, site)]
            r = values[i] - b
            z = r / scale
            if z > 3.5 and r > 0.12 * max(b, 0.1 * mean_base):
                flagged.append((i, r, z))

        # Group flagged hours into events. Gaps of up to 16h are bridged so that a problem
        # recurring every night (or a drift that dips during the day) is reported once.
        groups: list[list[tuple[int, float, float]]] = []
        for item in flagged:
            if groups and item[0] - groups[-1][-1][0] <= 16:
                groups[-1].append(item)
            else:
                groups.append([item])

        events = []
        for g in groups:
            if len(g) < 3 and max(x[2] for x in g) < 12:
                continue  # isolated blips are noise
            idxs = [x[0] for x in g]
            excess = sum(x[1] for x in g)
            cost = sum(self._cost(x[1], self.ts(x[0]).hour) for x in g)
            if cost < 100:
                continue  # not worth an operator's attention
            peak_dev = max(x[1] / max(base[self._slot(x[0], site)], 1e-6) for x in g) * 100
            pattern = self._pattern(site, idxs, g)
            hours = len(idxs)
            sev = ("critical" if cost > 4000 else "high" if cost > 1200 else "medium" if cost > 300 else "low")
            events.append(AnomalyEvent(
                id="", site_id=site.id, site_name=site.name, meter_id=meter.id, meter_name=meter.name,
                category=meter.category, pattern=pattern, start=self.ts(idxs[0]).isoformat(),
                end=(self.ts(idxs[-1]) + timedelta(hours=1)).isoformat(), hours=hours,
                excess_kwh=round(excess, 1), excess_cost_mad=round(cost, 1),
                peak_deviation_pct=round(peak_dev, 1), max_zscore=round(max(x[2] for x in g), 1),
                severity=sev, description=_describe(pattern, meter, hours, peak_dev),
            ))
        return events

    def _pattern(self, site: Site, idxs: list[int], g: list) -> str:
        times = [self.ts(i) for i in idxs]
        span = idxs[-1] - idxs[0] + 1
        off_hours = sum(1 for t in times if not (site.business_hours[0] <= t.hour < site.business_hours[1]))
        non_working = sum(1 for t in times if t.weekday() not in site.working_days)
        # Weekend: (almost) the whole event sits on non-working days. An event spanning several
        # nights, even across a weekend, is a night-schedule problem instead.
        if non_working >= len(times) * 0.85:
            return "weekend_operation"
        if span >= 72 and len(times) >= span * 0.6:
            first = statistics.fmean(x[2] for x in g[: max(1, len(g) // 4)])
            last = statistics.fmean(x[2] for x in g[-max(1, len(g) // 4):])
            return "drift" if last > first * 1.6 else "sustained_increase"
        if off_hours >= len(times) * 0.6:
            return "night_operation"
        return "spike"


def _describe(pattern: str, meter: Meter, hours: int, peak_dev: float) -> str:
    what = meter.name
    return {
        "night_operation": f"{what} running outside business hours for {hours}h (up to +{peak_dev:.0f}% vs baseline).",
        "weekend_operation": f"{what} active on a non-working day for {hours}h (up to +{peak_dev:.0f}%).",
        "sustained_increase": f"{what} consumption elevated continuously for {hours}h (+{peak_dev:.0f}% peak) "
                              f"- typical of a leak or a stuck setpoint.",
        "drift": f"{what} consumption drifting upward over {hours}h (+{peak_dev:.0f}% at worst) "
                 f"- progressive efficiency degradation.",
        "spike": f"{what} short spike of +{peak_dev:.0f}% over {hours}h.",
    }[pattern]


# --------------------------------------------------------------------------- simulation


def _profile(site: Site, meter: Meter, t: datetime, day: int, rng: random.Random, sun: list[float]) -> float:
    h = t.hour
    wd = t.weekday()
    working = wd in site.working_days
    open_ = working and site.business_hours[0] <= h < site.business_hours[1]
    # outdoor temperature: daily sinusoid, warmer in the afternoon + small trend
    temp = 22 + 6 * math.sin((h - 9) / 24 * 2 * math.pi) + 1.5 * math.sin(day / 5)
    if site.city == "Marrakech":
        temp += 4
    cooling_need = max(temp - 19, 0)
    noise = rng.gauss(1.0, 0.04)
    c = meter.category

    if site.type == "office":
        if c == "hvac":
            v = (18 + 9 * cooling_need) if open_ else 14
        elif c == "lighting":
            v = 95 if open_ else 12
        elif c == "it":
            v = 120 if open_ else 55
        else:
            v = 30 if open_ else 8
    elif site.type == "industrial":
        shift = working and site.business_hours[0] <= h < site.business_hours[1]
        if c == "compressed_air":
            v = 240 if shift else 30
        elif c == "process":
            v = 820 if shift else 60
        elif c == "hvac":
            v = (60 + 12 * cooling_need) if shift else 25
        elif c == "lighting":
            v = 110 if shift else 15
        else:  # solar
            cloud = 0.55 if day % 9 == 4 else 1.0
            return max(0.0, 420 * sun[h] * cloud * rng.gauss(1.0, 0.05))
    elif site.type == "data_center":
        it = 600 * (1 + 0.06 * math.sin((h - 14) / 24 * 2 * math.pi))
        if c == "it":
            v = it
        elif c == "cooling":
            v = it * (0.32 + 0.004 * cooling_need)
        else:
            v = it * 0.085
    else:  # healthcare
        day_part = 7 <= h < 20
        if c == "hvac":
            v = 90 + 10 * cooling_need + (40 if day_part else 0)
        elif c == "medical":
            v = 160 if day_part else 70
        elif c == "lighting":
            v = 70 if day_part else 30
        else:
            v = 45 + (35 if h in (6, 7, 8, 19, 20) else 0)
    return max(0.0, v * noise)


def build_dataset(days: int = 30, seed: int = 42, end: datetime | None = None) -> EnergyDataset:
    rng = random.Random(seed)
    if end is None:
        end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0, hour=0)
    start = end - timedelta(days=days)
    sun = [max(0.0, math.sin((h - 6) / 12 * math.pi)) if 6 <= h <= 18 else 0.0 for h in range(24)]
    ds = EnergyDataset(start=start, days=days)
    for site in SITES:
        for meter in site.meters:
            ds.series[meter.id] = [
                _profile(site, meter, start + timedelta(hours=i), i // 24, rng, sun) for i in range(days * 24)
            ]
    _inject_anomalies(ds)
    return ds


def _inject_anomalies(ds: EnergyDataset) -> None:
    d = ds.days

    def apply(meter_id: str, kind: str, desc: str, d0: int, d1: int, fn) -> None:
        values = ds.series[meter_id]
        for i in range(d0 * 24, min(d1 * 24, ds.hours)):
            values[i] = fn(i, ds.ts(i), values[i])
        ds.injected.append(InjectedAnomaly(meter_id, kind, desc, d0, d1 - 1))

    # A1 - HQ HVAC left running at night for 3 nights (BMS schedule override)
    apply("HQ-CASA-HVAC", "night_operation", "HVAC schedule override left on at night", d - 12, d - 9,
          lambda i, t, v: v * 4.2 if (t.hour >= 20 or t.hour < 6) else v)
    # A2 - compressed-air leak at the plant, ongoing since 6 days
    apply("PLANT-KEN-AIR", "sustained_increase", "Compressed-air leak (ongoing)", d - 6, d,
          lambda i, t, v: v + 95)
    # A3 - data center cooling degradation (PUE drift) over the last 7 days
    start_drift = (d - 7) * 24
    apply("DC-RABAT-COOL", "drift", "Cooling efficiency degradation (fouled filters / setpoint drift)", d - 7, d,
          lambda i, t, v: v * (1 + 0.38 * (i - start_drift) / (7 * 24)))
    # A4 - HQ lighting left on during the last weekend
    last_weekend = [k for k in range(d - 8, d) if ds.ts(k * 24).weekday() == 5][-1]
    apply("HQ-CASA-LGT", "weekend_operation", "Lighting left on during the weekend", last_weekend,
          last_weekend + 2, lambda i, t, v: v * 6.5)
    # A5 - clinic HVAC chiller fault: 4-hour spike
    apply("CLINIC-MRK-HVAC", "spike", "Chiller fault / short cycling", d - 5, d - 4,
          lambda i, t, v: v * 2.3 if 13 <= t.hour < 17 else v)


@lru_cache
def get_dataset() -> EnergyDataset:
    from app.config import get_settings

    s = get_settings()
    return build_dataset(days=s.data_days, seed=s.data_seed)


def live_reading(ds: EnergyDataset, site_id: str | None = None) -> dict:
    """Synthetic real-time power reading derived from the same profiles, for the live gauge."""
    now = datetime.now(UTC)
    rng = random.Random(int(now.timestamp() // 5))
    sun = [max(0.0, math.sin((h - 6) / 12 * math.pi)) if 6 <= h <= 18 else 0.0 for h in range(24)]
    per_site = {}
    for site in SITES:
        if site_id and site.id != site_id:
            continue
        load = solar = 0.0
        for m in site.meters:
            v = _profile(site, m, now, ds.days, rng, sun)
            if m.generation:
                solar += v
            else:
                load += v
        if site.id == "PLANT-KEN" and any(a.meter_id == "PLANT-KEN-AIR" for a in ds.injected):
            load += 95  # the leak is still ongoing
        per_site[site.id] = {"load_kw": round(load, 1), "solar_kw": round(solar, 1)}
    total = sum(v["load_kw"] for v in per_site.values())
    solar_total = sum(v["solar_kw"] for v in per_site.values())
    return {"timestamp": now.isoformat(timespec="seconds"), "load_kw": round(total, 1),
            "solar_kw": round(solar_total, 1), "grid_kw": round(max(total - solar_total, 0), 1),
            "tariff_period": tariff_period(now.hour),
            "price_mad_kwh": TARIFF_MAD_PER_KWH[tariff_period(now.hour)], "sites": per_site}
