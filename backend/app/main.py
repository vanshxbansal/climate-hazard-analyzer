from __future__ import annotations

import csv
import io
import logging
import re
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import openmeteo, service
from .config import settings
from .db import get_db
from .ratelimit import client_ip, limiter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")

app = FastAPI(title="Climate Hazard Trend Analyzer", version="1.0.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=settings.origins, allow_methods=["GET"], allow_headers=["*"], max_age=600)

HEAVY = ("/api/analysis", "/api/export.csv")


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path != "/api/health":
        ip = client_ip(request)
        heavy = path in HEAVY
        wait = limiter.check(f"{ip}:{'h' if heavy else 'l'}",
                             settings.heavy_rate_limit_per_minute if heavy else settings.rate_limit_per_minute)
        if wait:
            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429, headers={"Retry-After": str(int(wait))})
    resp = await call_next(request)
    resp.headers.update({
        "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "strict-origin-when-cross-origin",
        "Content-Security-Policy": ("default-src 'self'; img-src 'self' data: https://tile.openstreetmap.org; "
                                    "style-src 'self' 'unsafe-inline'; "
                                    "script-src 'self'; connect-src 'self'; frame-ancestors 'none'"),
    })
    if path.startswith("/api/") and "cache-control" not in resp.headers:
        resp.headers["Cache-Control"] = "public, max-age=300" if path.startswith("/api/analysis") else "no-store"
    return resp


@app.exception_handler(RequestValidationError)
async def _validation(_: Request, exc: RequestValidationError):
    msg = "; ".join(f"{'.'.join(str(p) for p in e['loc'][1:])}: {e['msg']}" for e in exc.errors())
    return JSONResponse({"detail": f"Invalid input: {msg}"}, status_code=422)


@app.exception_handler(openmeteo.UpstreamBusy)
async def _busy(_: Request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=503, headers={"Retry-After": "60"})


@app.exception_handler(openmeteo.UpstreamError)
async def _upstream(_: Request, exc):
    log.warning("upstream failure: %s", exc)
    return JSONResponse({"detail": "The weather data provider is unavailable, please retry shortly."}, status_code=502)


@app.exception_handler(ValueError)
async def _value(_: Request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=422)


_NAME_RE = re.compile(r"^[\w\s,.'()\-]{1,100}$", re.UNICODE)


class Region:
    """Validated region: a point or a bounding box (analysed at its centre cell)."""


def resolve_region(lat, lon, south, west, north, east, name):
    if name is not None and not _NAME_RE.match(name):
        raise ValueError("Region name contains unsupported characters")
    box = [south, west, north, east]
    if any(v is not None for v in box):
        if any(v is None for v in box):
            raise ValueError("A bounding box needs south, west, north and east")
        if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
            raise ValueError("Invalid bounding box (need south<north, west<east within valid ranges)")
        if (north - south) > 10 or (east - west) > 10:
            raise ValueError("Bounding box too large: max 10 x 10 degrees")
        return (south + north) / 2, (west + east) / 2, name or "Custom box"
    if lat is None or lon is None:
        raise ValueError("Provide lat & lon or a bounding box")
    return lat, lon, name


def _common(lat, lon, south, west, north, east, name, start, end, bs, be, pct, min_days):
    if end < start:
        raise ValueError("end year must be >= start year")
    if end - start + 1 < 5:
        raise ValueError("Select at least 5 years")
    if bs > be or be - bs + 1 < 20:
        raise ValueError("Baseline must span at least 20 years")
    c_lat, c_lon, nm = resolve_region(lat, lon, south, west, north, east, name)
    return service.run_analysis(c_lat, c_lon, start, end, bs, be, pct, min_days, nm or "")


Y = dict(ge=settings.min_year, le=settings.max_year)


@app.get("/api/health")
def health():
    get_db().command("ping") if settings.mongodb_uri else None
    return {"status": "ok", "storage": "mongodb" if settings.mongodb_uri else "in-memory"}


@app.get("/api/config")
def config():
    return {"min_year": settings.min_year, "max_year": settings.max_year,
            "default_baseline": [1991, 2020], "hazards": ["heatwave"]}


@app.get("/api/geocode")
def geocode(q: str = Query(..., min_length=2, max_length=80)):
    q = q.strip()
    if not _NAME_RE.match(q):
        raise ValueError("Search text contains unsupported characters")
    db = get_db()
    key = q.lower()
    hit = db.geocode.find_one({"_id": key})
    if hit:
        return {"results": hit["results"], "cached": True}
    results = openmeteo.geocode(q)
    from datetime import datetime, timezone
    db.geocode.replace_one({"_id": key}, {"_id": key, "results": results, "created_at": datetime.now(timezone.utc)}, upsert=True)
    return {"results": results, "cached": False}


@app.get("/api/analysis")
def analysis(
    lat: Optional[float] = Query(None, ge=-90, le=90), lon: Optional[float] = Query(None, ge=-180, le=180),
    south: Optional[float] = Query(None, ge=-90, le=90), west: Optional[float] = Query(None, ge=-180, le=180),
    north: Optional[float] = Query(None, ge=-90, le=90), east: Optional[float] = Query(None, ge=-180, le=180),
    name: Optional[str] = Query(None, max_length=100),
    hazard: str = Query("heatwave", pattern="^heatwave$"),
    start: int = Query(1990, **Y), end: int = Query(2020, **Y),
    baseline_start: int = Query(1991, **Y), baseline_end: int = Query(2020, **Y),
    percentile: float = Query(95.0, ge=80, le=99.9), min_days: int = Query(3, ge=2, le=10),
):
    return _common(lat, lon, south, west, north, east, name, start, end, baseline_start, baseline_end, percentile, min_days)


def _csv_safe(v):
    s = str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") and not re.fullmatch(r"-?\d+(\.\d+)?", s) else s


@app.get("/api/export.csv")
def export_csv(
    lat: Optional[float] = Query(None, ge=-90, le=90), lon: Optional[float] = Query(None, ge=-180, le=180),
    south: Optional[float] = Query(None, ge=-90, le=90), west: Optional[float] = Query(None, ge=-180, le=180),
    north: Optional[float] = Query(None, ge=-90, le=90), east: Optional[float] = Query(None, ge=-180, le=180),
    name: Optional[str] = Query(None, max_length=100),
    start: int = Query(1990, **Y), end: int = Query(2020, **Y),
    baseline_start: int = Query(1991, **Y), baseline_end: int = Query(2020, **Y),
    percentile: float = Query(95.0, ge=80, le=99.9), min_days: int = Query(3, ge=2, le=10),
):
    r = _common(lat, lon, south, west, north, east, name, start, end, baseline_start, baseline_end, percentile, min_days)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["# Climate Hazard Trend Analyzer - heatwaves"])
    w.writerow(["# region", _csv_safe(r["region"]["name"]), "cell", r["region"]["cell"]["lat"], r["region"]["cell"]["lon"]])
    w.writerow(["# headline", _csv_safe(r["insights"]["headline"])])
    w.writerow(["# source", r["meta"]["source"]])
    w.writerow([])
    w.writerow(["year", "events", "heatwave_days", "max_duration", "mean_duration", "mean_intensity_C", "coverage", "reliable"])
    for y in r["yearly"]:
        w.writerow([y["year"], y["events"], y["hw_days"], y["max_duration"], y["mean_duration"],
                    "" if y["mean_intensity"] is None else y["mean_intensity"], y["coverage"], y["reliable"]])
    w.writerow([])
    w.writerow(["event_start", "event_end", "duration_days", "peak_tmax_C", "mean_exceedance_C"])
    for e in r["events"]:
        w.writerow([e["start"], e["end"], e["duration"], e["peak_tmax"], e["mean_exceedance"]])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="heatwave-analysis.csv"'})


# ---- serve the built React app (single-service deploy) -------------------------------------
_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(404)
        f = (_DIST / full_path).resolve()
        if full_path and f.is_file() and _DIST in f.parents:
            return FileResponse(f)
        return FileResponse(_DIST / "index.html")
