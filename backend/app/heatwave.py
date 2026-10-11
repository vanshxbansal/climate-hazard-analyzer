"""Heatwave definition and event detection.

Definition (documented in the README):
  * threshold(d) = 95th percentile of daily Tmax on calendar day d, taken over a
    +/-7-day window (15 days) around d across all baseline years (default 1991-2020),
    which gives ~450 samples per day and removes sampling noise from a single date.
  * a heatwave = >= 3 consecutive days with Tmax strictly above threshold(d).
  * a missing/invalid day is never "hot" and therefore ends a run (conservative:
    gaps can only under-count, never invent events).
"""
from __future__ import annotations

import warnings
from typing import Dict, List, Tuple

import numpy as np

HALF_WINDOW = 7
MIN_SAMPLES = 100  # of ~450 possible; below this a calendar-day threshold is not trusted


def is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def year_dates(year: int) -> np.ndarray:
    return np.arange(np.datetime64(f"{year}-01-01"), np.datetime64(f"{year + 1}-01-01"), dtype="datetime64[D]")


def calendar_index(dates: np.ndarray) -> np.ndarray:
    """Map dates to 0..365 where 29 Feb always owns index 59 (leap-aware, so Mar 1 is always 60)."""
    years = dates.astype("datetime64[Y]")
    doy = (dates - years.astype("datetime64[D]")).astype(int)
    yr = years.astype(int) + 1970
    leap = np.array([is_leap(int(y)) for y in yr]) if yr.size else np.array([], dtype=bool)
    return doy + ((~leap) & (doy >= 59)).astype(int)


def build_series(per_year: Dict[int, List[float]]) -> Tuple[np.ndarray, np.ndarray]:
    """Concatenate per-year arrays into one contiguous daily series (NaN where a year is absent)."""
    y0, y1 = min(per_year), max(per_year)
    dates = np.concatenate([year_dates(y) for y in range(y0, y1 + 1)])
    vals = np.concatenate([
        np.asarray(per_year[y], dtype=float) if y in per_year else np.full(len(year_dates(y)), np.nan)
        for y in range(y0, y1 + 1)
    ])
    return dates, vals


def percentile_thresholds(dates: np.ndarray, tmax: np.ndarray, base_start: int, base_end: int,
                          pct: float = 95.0) -> np.ndarray:
    """Return a (366,) array of calendar-day thresholds; NaN where the baseline is too sparse."""
    yrs = dates.astype("datetime64[Y]").astype(int) + 1970
    sel = (yrs >= base_start) & (yrs <= base_end)
    idx = calendar_index(dates[sel])
    yy = yrs[sel]
    vals = tmax[sel]

    grid = np.full((base_end - base_start + 1, 366), np.nan)
    grid[yy - base_start, idx] = vals

    thr = np.full(366, np.nan)
    offsets = np.arange(-HALF_WINDOW, HALF_WINDOW + 1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for d in range(366):
            sample = grid[:, (d + offsets) % 366].ravel()
            sample = sample[~np.isnan(sample)]
            if sample.size >= MIN_SAMPLES:
                thr[d] = np.percentile(sample, pct)
    return thr


def detect_events(dates: np.ndarray, tmax: np.ndarray, thr: np.ndarray, min_days: int = 3) -> List[dict]:
    """Find runs of >= min_days consecutive days with tmax > threshold."""
    t = thr[calendar_index(dates)]
    with np.errstate(invalid="ignore"):
        hot = tmax > t  # NaN comparisons are False -> gaps break runs
    padded = np.concatenate(([False], hot, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    starts, ends = edges[0::2], edges[1::2]  # [start, end) index pairs

    events = []
    for s, e in zip(starts, ends):
        n = int(e - s)
        if n < min_days:
            continue
        exceed = tmax[s:e] - t[s:e]
        events.append({
            "start": str(dates[s]), "end": str(dates[e - 1]), "year": int(str(dates[s])[:4]),
            "duration": n,
            "peak_tmax": round(float(np.max(tmax[s:e])), 1),
            "mean_exceedance": round(float(np.mean(exceed)), 2),
            "max_exceedance": round(float(np.max(exceed)), 2),
            "cumulative_exceedance": round(float(np.sum(exceed)), 1),
        })
    return events


def yearly_metrics(events: List[dict], coverage: Dict[int, float], years: List[int],
                   min_coverage: float) -> List[dict]:
    rows = []
    for y in years:
        ev = [e for e in events if e["year"] == y]
        days = sum(e["duration"] for e in ev)
        cov = coverage.get(y, 0.0)
        rows.append({
            "year": y,
            "events": len(ev),
            "hw_days": days,
            "max_duration": max((e["duration"] for e in ev), default=0),
            "mean_duration": round(days / len(ev), 2) if ev else 0.0,
            # intensity = mean exceedance above the threshold over all heatwave days of the year
            "mean_intensity": round(sum(e["mean_exceedance"] * e["duration"] for e in ev) / days, 2) if days else None,
            "cumulative_intensity": round(sum(e["cumulative_exceedance"] for e in ev), 1),
            "coverage": round(cov, 3),
            "reliable": cov >= min_coverage,
        })
    return rows
