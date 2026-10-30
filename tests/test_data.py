from datetime import UTC, datetime

from app.data.energy_data import SITES, build_dataset, tariff_period

END = datetime(2026, 10, 1, tzinfo=UTC)


def test_dataset_is_deterministic():
    a = build_dataset(seed=42, end=END)
    b = build_dataset(seed=42, end=END)
    assert a.series == b.series
    assert len(a.series) == sum(len(s.meters) for s in SITES)
    assert all(len(v) == 30 * 24 for v in a.series.values())


def test_detector_finds_every_injected_anomaly_without_false_positives():
    for seed in (1, 42, 99):
        ds = build_dataset(seed=seed, end=END)
        truth = {(a.meter_id, a.kind) for a in ds.injected}
        found = {(a.meter_id, a.pattern) for a in ds.anomalies()}
        assert found == truth, f"seed={seed}"


def test_anomalies_are_ranked_and_quantified():
    ds = build_dataset(end=END)
    anoms = ds.anomalies()
    costs = [a.excess_cost_mad for a in anoms]
    assert costs == sorted(costs, reverse=True)
    assert [a.id for a in anoms] == [f"ANOM-{i:03d}" for i in range(1, len(anoms) + 1)]
    assert all(a.excess_kwh > 0 and a.severity in {"low", "medium", "high", "critical"} for a in anoms)


def test_summary_is_consistent():
    ds = build_dataset(end=END)
    total = ds.summary(None, 30)
    per_site = sum(ds.summary(s.id, 30)["consumption_kwh"] for s in SITES)
    assert abs(total["consumption_kwh"] - per_site) < 1
    assert abs(sum(total["by_category"].values()) - total["consumption_kwh"]) < 1
    assert total["solar_generation_kwh"] > 0
    assert ds.summary("HQ-CASA", 7)["solar_generation_kwh"] == 0


def test_pue_drift_is_visible():
    pue = build_dataset(end=END).data_center_pue(7)
    assert pue["current_pue"] > pue["baseline_pue"] + 0.05


def test_tariff_periods():
    assert tariff_period(19) == "peak"
    assert tariff_period(10) == "standard"
    assert tariff_period(23) == tariff_period(3) == "off_peak"


def test_detector_is_robust_to_calendar_alignment():
    """The same injected problems must be found whatever weekday the dataset ends on."""
    from datetime import timedelta

    for shift in range(7):
        ds = build_dataset(seed=8, end=END + timedelta(days=shift))
        assert {(a.meter_id, a.pattern) for a in ds.anomalies()} == {(a.meter_id, a.kind) for a in ds.injected}
