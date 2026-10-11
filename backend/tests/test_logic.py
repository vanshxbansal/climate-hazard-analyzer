import numpy as np
import pytest

from app import heatwave, quality, trends


def synthetic(years, seed=0, warming=0.0):
    """Seasonal cycle + noise (+ optional linear warming) as {year: [..]}."""
    rng = np.random.default_rng(seed)
    out = {}
    for i, y in enumerate(years):
        n = len(heatwave.year_dates(y))
        d = np.arange(n)
        out[y] = list(25 + 10 * np.sin(2 * np.pi * (d - 100) / n) + rng.normal(0, 2, n) + warming * i)
    return out


def test_calendar_index_leap_alignment():
    d = np.array(["2019-03-01", "2020-02-29", "2020-03-01", "2019-12-31", "2020-12-31"], dtype="datetime64[D]")
    assert list(heatwave.calendar_index(d)) == [60, 59, 60, 364 + 1, 365]


def test_threshold_is_95th_percentile_of_baseline():
    per_year = synthetic(range(1991, 2021))
    dates, vals = heatwave.build_series(per_year)
    thr = heatwave.percentile_thresholds(dates, vals, 1991, 2020)
    assert not np.isnan(thr).any()
    exceed = (vals > thr[heatwave.calendar_index(dates)]).mean()
    assert 0.04 < exceed < 0.06  # ~5% of baseline days exceed by construction


def test_detects_only_runs_of_three_or_more():
    dates = np.arange(np.datetime64("2001-01-01"), np.datetime64("2001-01-21"), dtype="datetime64[D]")
    t = np.full(len(dates), 20.0)
    thr = np.full(366, 30.0)
    t[2:4] = 35      # 2-day run: not a heatwave
    t[8:12] = 36     # 4-day run: heatwave
    t[14] = 40       # single day
    t[16:19] = 33    # exactly 3 days: heatwave
    ev = heatwave.detect_events(dates, t, thr, 3)
    assert [(e["start"], e["duration"]) for e in ev] == [("2001-01-09", 4), ("2001-01-17", 3)]
    assert ev[0]["peak_tmax"] == 36.0 and ev[0]["mean_exceedance"] == 6.0


def test_missing_day_breaks_a_run():
    dates = np.arange(np.datetime64("2001-06-01"), np.datetime64("2001-06-08"), dtype="datetime64[D]")
    t = np.array([35, 35, np.nan, 35, 35, 20, 20.0])
    ev = heatwave.detect_events(dates, t, np.full(366, 30.0), 3)
    assert ev == []  # gap must not be bridged


def test_quality_removes_outliers_and_counts_gaps():
    x = np.full(60, 25.0)
    x[5] = np.nan
    x[10] = 999  # impossible
    x[20] = 70   # beyond physical bound
    x[30] = 55   # 30 C jump from neighbours: spike
    rep = quality.QualityReport()
    out = quality.clean(x, rep)
    assert np.isnan(out[[5, 10, 20, 30]]).all()
    assert (rep.missing_days, rep.out_of_range, rep.spikes) == (1, 2, 1)


def test_mann_kendall_detects_trend_and_noise():
    inc = trends.mann_kendall(np.arange(30, dtype=float) + np.random.default_rng(1).normal(0, 1, 30))
    assert inc["trend"] == "increasing" and inc["p"] < 0.001
    flat = trends.mann_kendall(np.random.default_rng(2).normal(0, 1, 30))
    assert flat["trend"] == "no trend"
    assert trends.mann_kendall(np.zeros(20))["trend"] == "no trend"  # all ties


def test_analyse_metric_percent_change_and_guard_rails():
    years = list(range(1990, 2020))
    vals = [2.0] * 10 + [3.0] * 10 + [4.0] * 10
    r = trends.analyse_metric(years, vals, "d")
    assert r["pct_change"] == 100.0 and r["significant"]
    assert trends.analyse_metric(years[:6], vals[:6], "d")["enough_data"] is False
    z = trends.analyse_metric(years, [0.0] * 30, "d")
    assert z["pct_change"] is None and not z["significant"]


def test_rolling_mean_trailing_and_gap_aware():
    assert trends.rolling_mean([1, 2, 3, 4, 5, 6], 3) == [None, None, 2.0, 3.0, 4.0, 5.0]
    assert trends.rolling_mean([1, None, 3, 4, 5], 3) == [None, None, None, None, 4.0]


def test_warming_signal_is_recovered_end_to_end():
    per_year = synthetic(range(1980, 2021), seed=3, warming=0.08)
    dates, vals = heatwave.build_series(per_year)
    thr = heatwave.percentile_thresholds(dates, vals, 1991, 2020)
    ev = heatwave.detect_events(dates, vals, thr, 3)
    rows = heatwave.yearly_metrics(ev, {y: 1.0 for y in per_year}, list(per_year), 0.9)
    res = trends.analyse_metric([r["year"] for r in rows], [r["hw_days"] for r in rows], "d")
    assert res["mann_kendall"]["trend"] == "increasing" and res["significant"]
