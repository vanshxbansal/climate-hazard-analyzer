"""Orchestration: cache lookup -> ingest missing years -> detect -> trend -> store."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Tuple

import numpy as np

from . import heatwave, insights, openmeteo, quality, trends
from .config import settings
from .db import get_db

log = logging.getLogger("service")
ALGO_VERSION = 2  # bump to invalidate stored analyses when the logic changes

_cell_locks: Dict[str, threading.Lock] = {}
_cell_locks_guard = threading.Lock()


def snap(v: float) -> float:
    """Snap to the 0.25 degree ERA5 grid so nearby clicks share one cache entry."""
    return round(round(v * 4) / 4, 2)


def cell_key(lat: float, lon: float) -> str:
    return f"{snap(lat):.2f},{snap(lon):.2f}"


def _lock_for(key: str) -> threading.Lock:
    with _cell_locks_guard:
        return _cell_locks.setdefault(key, threading.Lock())


def ingest_years(lat: float, lon: float, years: List[int]) -> Tuple[Dict[int, List], int]:
    """Return {year: [daily tmax]} for the cell, fetching only years not yet in MongoDB."""
    db = get_db()
    key = cell_key(lat, lon)
    c_lat, c_lon = map(float, key.split(","))
    have: Dict[int, List] = {}
    with _lock_for(key):  # single-flight: concurrent requests for a cell share one upstream fetch
        for d in db.daily_tmax.find({"cell": key, "year": {"$in": years}}):
            if len(d["tmax"]) == len(heatwave.year_dates(d["year"])):
                have[d["year"]] = d["tmax"]
        missing = [y for y in years if y not in have]
        fetched = 0
        if missing:
            # one request per contiguous block of missing years
            blocks, start = [], missing[0]
            for a, b in zip(missing, missing[1:] + [None]):
                if b != a + 1:
                    blocks.append((start, a))
                    start = b
            for y0, y1 in blocks:
                raw = openmeteo.fetch_daily_tmax(c_lat, c_lon, y0, y1)
                for y in range(y0, y1 + 1):
                    arr = [raw.get(str(d)) for d in heatwave.year_dates(y)]
                    if all(v is None for v in arr):
                        continue  # archive has nothing for this year: do not cache an empty year
                    db.daily_tmax.update_one(
                        {"cell": key, "year": y},
                        {"$set": {"tmax": arr, "fetched_at": datetime.now(timezone.utc), "source": "open-meteo/era5"}},
                        upsert=True)
                    have[y] = arr
                    fetched += 1
    return have, fetched


def _nan_array(vals: List) -> np.ndarray:
    return np.array([np.nan if v is None else v for v in vals], dtype=float)


def analysis_id(p: dict) -> str:
    blob = json.dumps({**p, "v": ALGO_VERSION}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


def run_analysis(lat: float, lon: float, start: int, end: int, base_start: int, base_end: int,
                 percentile: float, min_days: int, region_name: str) -> dict:
    t0 = time.perf_counter()
    db = get_db()
    key = cell_key(lat, lon)
    params = {"cell": key, "start": start, "end": end, "bs": base_start, "be": base_end,
              "pct": percentile, "min_days": min_days}
    aid = analysis_id(params)

    doc = db.analyses.find_one({"_id": aid})
    if doc:
        out = doc["result"]
        out["region"]["name"] = region_name or out["region"]["name"]
        out["meta"] = {**out["meta"], "cached": True, "runtime_ms": round((time.perf_counter() - t0) * 1000)}
        return out

    need = sorted(set(range(start, end + 1)) | set(range(base_start, base_end + 1)))
    raw, fetched = ingest_years(lat, lon, need)
    if not all(y in raw for y in range(base_start, base_end + 1)):
        got = sorted(y for y in raw if base_start <= y <= base_end)
        if len(got) < 20 and (base_end - base_start + 1) >= 20:
            raise ValueError("Not enough baseline data for this location (ERA5 returned too few years)")
    if not any(y in raw for y in range(start, end + 1)):
        raise ValueError("No data available for the selected period at this location")

    report = quality.QualityReport()
    cleaned: Dict[int, List[float]] = {}
    coverage: Dict[int, float] = {}
    for y in sorted(raw):
        arr = quality.clean(_nan_array(raw[y]), report)
        cleaned[y] = arr
        coverage[y] = float(np.mean(~np.isnan(arr)))

    dates, series = heatwave.build_series(cleaned)
    thr = heatwave.percentile_thresholds(dates, series, base_start, base_end, percentile)
    if np.isnan(thr).mean() > 0.2:
        raise ValueError("Baseline too sparse to compute reliable percentile thresholds")

    in_range = (dates.astype("datetime64[Y]").astype(int) + 1970 >= start) & \
               (dates.astype("datetime64[Y]").astype(int) + 1970 <= end)
    events = heatwave.detect_events(dates[in_range], series[in_range], thr, min_days)
    years = [y for y in range(start, end + 1) if y in cleaned]
    rows = heatwave.yearly_metrics(events, coverage, years, quality.MIN_YEAR_COVERAGE)

    report.unreliable_years = [r["year"] for r in rows if not r["reliable"]]
    metric_defs = {
        "events": ("events", "events/yr"), "hw_days": ("hw_days", "days/yr"),
        "mean_duration": ("mean_duration", "days"), "mean_intensity": ("mean_intensity", "°C"),
        "max_duration": ("max_duration", "days"),
    }
    trend_out = {}
    for name, (field_, unit) in metric_defs.items():
        vals = [r[field_] if r["reliable"] else None for r in rows]
        if name in ("mean_duration", "mean_intensity"):  # undefined in years without events
            vals = [v if (r["events"] > 0 and v is not None) else None for v, r in zip(vals, rows)]
            # for these two, a year with no events is not "0": it is excluded rather than biasing the mean
        trend_out[name] = trends.analyse_metric([r["year"] for r in rows], vals, unit)
    for field_ in ("events", "hw_days"):
        vals = [r[field_] if r["reliable"] else None for r in rows]
        for r, rm in zip(rows, trends.rolling_mean(vals, 5)):
            r[f"{field_}_rolling5"] = rm

    qd = report.as_dict()
    name = region_name or f"{snap(lat):.2f}, {snap(lon):.2f}"
    ins = insights.build(name, trend_out, {"start": base_start, "end": base_end}, years, qd)

    result = {
        "region": {"name": name, "lat": lat, "lon": lon, "cell": {"lat": snap(lat), "lon": snap(lon)}},
        "params": {"hazard": "heatwave", "start": start, "end": end, "percentile": percentile,
                   "min_days": min_days, "baseline": {"start": base_start, "end": base_end}},
        "yearly": rows,
        "events": sorted(events, key=lambda e: e["start"]),
        "trends": trend_out,
        "insights": ins,
        "threshold_by_day": [None if np.isnan(v) else round(float(v), 1) for v in thr],
        "data_quality": qd,
        "meta": {"source": "Open-Meteo archive (ERA5 reanalysis), daily max 2 m temperature",
                 "cached": False, "years_fetched_upstream": fetched,
                 "computed_at": datetime.now(timezone.utc).isoformat(),
                 "runtime_ms": round((time.perf_counter() - t0) * 1000), "algo_version": ALGO_VERSION},
    }
    db.analyses.replace_one({"_id": aid}, {"_id": aid, "created_at": datetime.now(timezone.utc), "result": result},
                            upsert=True)
    return result
