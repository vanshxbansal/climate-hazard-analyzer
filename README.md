# Climate Hazard Trend Analyzer (Heatwaves)

Full-stack app that analyses 30+ years of daily weather for any place and shows whether heatwaves are becoming more frequent, longer or more intense, with statistics that say when a trend is *not* distinguishable from noise.

**Live app:** https://climate-hazard-analyzer-hbxo.onrender.com (free tier: first load after idle can take ~50 s) · **Demo video:** `<LOOM_URL>`

## Features
- **Region**: city/state search (Open-Meteo geocoding), click a point on a Leaflet map, or draw a lat-lon box (max 10°x10°).
- **Hazard**: Heatwave (default; other hazards shown as "planned").
- **Time range**: any 5+ years within 1950 to last complete year.
- **Charts** (Chart.js): events/heatwave-days/longest-event per year with 5-year rolling mean and regression line; duration and intensity; the 95th-percentile threshold climatology.
- **Insights**: plain-language headline ("heatwave days increased X% ...") that states statistical significance and caveats.
- **Bonus**: Leaflet map selection, CSV export (server) and PDF (print stylesheet, "Save as PDF"), OLS regression + Mann-Kendall test + Sen's slope.

## Architecture
```
React (Vite, Chart.js, Leaflet)  --/api-->  FastAPI  -->  MongoDB (Atlas)
                                              |  ^
                                              v  |  (only on cache miss)
                                        Open-Meteo archive (ERA5)
```
Single deployable: FastAPI serves the built React app and `/api/*`.

| Endpoint | Purpose |
|---|---|
| `GET /api/analysis` | lat/lon or south/west/north/east, start, end, baseline_*, percentile, min_days -> JSON summary, yearly series, events, trends, insights |
| `GET /api/export.csv` | same params, CSV of yearly metrics + events |
| `GET /api/geocode?q=` | place search (cached) |
| `GET /api/config`, `/api/health` | limits, liveness |

**MongoDB collections**
- `daily_tmax`: one document per (0.25° grid cell, year) holding that year's daily Tmax array. Raw-data cache; only missing years are ever fetched.
- `analyses`: computed result keyed by hash(cell, range, baseline, params, algorithm version), TTL 30 days. A repeat request is a single DB read (~1 ms) with no recompute.
- `geocode`: cached place lookups (TTL 90 days).

Request flow: `analyses` hit -> return. Miss -> read `daily_tmax`, fetch only missing years from Open-Meteo (single-flight lock per cell), clean, detect, trend, store, return.

## Data source
Open-Meteo Historical Weather API (`models=era5`): ECMWF ERA5 reanalysis, daily max 2 m temperature, ~0.25° grid, no API key. Requests are snapped to the 0.25° grid so nearby clicks share a cache entry.

## Hazard logic (heatwave)
1. **Threshold**: for each calendar day, the 95th percentile of Tmax over a ±7-day window (15 days x 30 years ≈ 450 samples) in the baseline period (default 1991-2020, the WMO climate normal; alternatively the selected period). Feb 29 is aligned so Mar 1 always maps to the same day.
2. **Event**: ≥3 consecutive days with Tmax strictly above that day's threshold (min days and percentile are API parameters).
3. **Yearly metrics** (attributed to the year the event starts): events, heatwave days, longest event, mean duration, mean intensity (°C above threshold), cumulative intensity.
4. **Trends**: OLS regression (slope/decade, p, R²), Mann-Kendall (tie-corrected) with Sen's slope, trailing 5-year rolling mean, and first-vs-last-period change (first/last ≈10 years). "Significant" is claimed only if Mann-Kendall **and** OLS both give p<0.05 over ≥10 reliable years.

## Data cleaning / noisy data
- Missing values (null) are kept as gaps; a gap **ends** a run, so gaps can only under-count, never create events.
- Physically impossible values (<-90 or >60 °C) and spikes (>15 °C from the 7-day local median) are removed and counted.
- Units are verified from the response (°F/K converted to °C).
- Years with <90% valid days are flagged `reliable=false`, shown grey, and excluded from trend statistics; every response includes a `data_quality` report.
- Thresholds are not computed for calendar days with <100 baseline samples.

## Security and performance
- All inputs validated (ranges, regex for names, box size, year bounds, allow-listed hazard) and rejected with 422; upstream hosts are fixed and only numeric values are interpolated (no SSRF).
- Per-IP sliding-window rate limits (60/min general, 12/min for analysis/export) and a global cap on outbound Open-Meteo calls (20/min) with retry/back-off and timeouts; upstream failure -> 502, budget exhausted -> 503 + `Retry-After`.
- Three cache layers: raw data in MongoDB, computed analyses in MongoDB, HTTP `Cache-Control` for analysis responses.
- Security headers (CSP, nosniff, frame deny), CORS restricted to configured origins, GET-only API, CSV formula-injection guard, no secrets in code.

## Run locally
```bash
# backend (Python 3.9+)
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # optional: set MONGODB_URI for real MongoDB
.venv/bin/uvicorn app.main:app --reload --port 8000
# frontend (dev server proxies /api to :8000)
cd frontend && npm install && npm run dev      # http://localhost:5173
# tests
cd backend && .venv/bin/python -m pytest -q    # 25 tests
```
Without `MONGODB_URI` the app uses in-memory `mongomock` (dev/tests only).

## Deploy (Render free tier + MongoDB Atlas free tier)
1. Create a free Atlas M0 cluster, a DB user, allow `0.0.0.0/0` network access, copy the connection string.
2. Push this repo to GitHub. On Render: New -> Blueprint -> select the repo (uses `render.yaml` + `Dockerfile`).
3. Set `MONGODB_URI` (and `ALLOWED_ORIGINS` to the Render URL). Deploy. The first request after idle takes ~30 s (free-tier cold start).

## Assumptions
- A region (city, state or box) is represented by **one ERA5 grid cell at its centre**, not an area average. State/box results therefore describe the centre, not the whole area.
- ERA5 is reanalysis, not station observation; it smooths local extremes.
- Only complete calendar years are analysed (the current year is excluded).
- The percentile threshold is by calendar day, so warm spells in the cool season can qualify (e.g. a 31 °C February spell in Phoenix). This follows the stated definition but is not what a local weather service would call a heatwave.
- When the selected period overlaps the baseline, ~5% of days exceed by construction; the UI says so.

## What I would improve with more time
- Area-averaged analysis over several grid cells for boxes/states; use region polygons.
- Warm-season filter or absolute temperature floor, and humidity-based indices (heat index / wet-bulb).
- Drought (SPI) and heavy-rainfall hazards behind the same pipeline; station data (NOAA) cross-check.
- Autocorrelation-aware trend tests (prewhitening), bootstrap confidence intervals on the slopes.
- Redis-backed rate limiting for multi-instance deploys, background pre-warming, frontend tests and code splitting.

## AI tools used
Claude Code (Claude Sonnet) was used to scaffold and write the backend, tests, frontend and this README from the case-study brief; I reviewed the code, ran the tests, and verified behaviour in a browser against live Open-Meteo data. (Edit this section to describe your own involvement accurately before submitting.)
