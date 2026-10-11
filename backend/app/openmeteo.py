"""Safe client for the Open-Meteo archive + geocoding APIs.

Only fixed upstream hosts are contacted and only validated numeric parameters are
interpolated, so user input can never steer the request (no SSRF surface).
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Dict, List, Optional

import httpx

from .config import settings

log = logging.getLogger("openmeteo")


class UpstreamError(Exception):
    pass


class UpstreamBusy(UpstreamError):
    pass


_calls: deque = deque()
_calls_lock = threading.Lock()


def _budget() -> None:
    """Global outbound budget so a burst of distinct cells cannot exhaust the free-tier quota."""
    now = time.monotonic()
    with _calls_lock:
        while _calls and now - _calls[0] > 60:
            _calls.popleft()
        if len(_calls) >= settings.upstream_max_per_minute:
            raise UpstreamBusy("Upstream request budget exhausted, retry in a minute")
        _calls.append(now)


def _get(url: str, params: dict) -> dict:
    _budget()
    last: Optional[Exception] = None
    for attempt in range(3):
        try:
            r = httpx.get(url, params=params, timeout=settings.upstream_timeout_s,
                          headers={"User-Agent": "climate-hazard-analyzer/1.0"})
            if r.status_code == 429 or r.status_code >= 500:
                raise UpstreamError(f"upstream HTTP {r.status_code}")
            if r.status_code >= 400:
                reason = r.json().get("reason", "bad request") if r.headers.get("content-type", "").startswith("application/json") else "bad request"
                raise UpstreamError(f"upstream rejected request: {reason}")
            return r.json()
        except (httpx.HTTPError, UpstreamError, ValueError) as exc:
            last = exc
            if isinstance(exc, UpstreamError) and "rejected" in str(exc):
                break  # 4xx will not get better with retries
            time.sleep(1.5 * (attempt + 1))
    raise UpstreamError(f"Open-Meteo unavailable: {last}")


def _to_celsius(values: List[Optional[float]], unit: str) -> List[Optional[float]]:
    """Normalise units defensively even though we request celsius."""
    u = (unit or "").strip().lower()
    if u in ("°c", "c", "celsius", ""):
        return values
    if u in ("°f", "f", "fahrenheit"):
        return [None if v is None else (v - 32) * 5 / 9 for v in values]
    if u in ("k", "kelvin"):
        return [None if v is None else v - 273.15 for v in values]
    raise UpstreamError(f"Unexpected temperature unit {unit!r}")


def fetch_daily_tmax(lat: float, lon: float, start_year: int, end_year: int) -> Dict[str, Optional[float]]:
    """Daily max 2 m temperature (deg C, ERA5 reanalysis) as {iso_date: value|None}."""
    data = _get(settings.openmeteo_archive_url, {
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
        "start_date": f"{start_year}-01-01", "end_date": f"{end_year}-12-31",
        "daily": "temperature_2m_max", "models": "era5",
        "temperature_unit": "celsius", "timezone": "auto",
    })
    try:
        times = data["daily"]["time"]
        vals = data["daily"]["temperature_2m_max"]
        unit = data.get("daily_units", {}).get("temperature_2m_max", "°C")
    except (KeyError, TypeError) as exc:
        raise UpstreamError("Unexpected Open-Meteo response shape") from exc
    if len(times) != len(vals):
        raise UpstreamError("Open-Meteo returned misaligned arrays")
    vals = _to_celsius(vals, unit)
    return dict(zip(times, vals))


def geocode(query: str, count: int = 6) -> List[dict]:
    data = _get(settings.openmeteo_geocode_url, {"name": query, "count": count, "language": "en", "format": "json"})
    out = []
    for r in data.get("results", []) or []:
        parts = [r.get("name"), r.get("admin1"), r.get("country")]
        out.append({
            "name": ", ".join(p for p in parts if p),
            "lat": r["latitude"], "lon": r["longitude"],
            "kind": r.get("feature_code", ""),
        })
    return out
