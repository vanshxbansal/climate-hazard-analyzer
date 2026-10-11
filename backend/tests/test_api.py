import pytest
from fastapi.testclient import TestClient

from app import main, openmeteo, service
from app.config import settings
from tests.test_logic import synthetic
from app import heatwave

calls = {"n": 0}


def fake_fetch(lat, lon, y0, y1):
    calls["n"] += 1
    data = synthetic(range(y0, y1 + 1), seed=5, warming=0.06)
    out = {}
    for y, arr in data.items():
        for d, v in zip(heatwave.year_dates(y), arr):
            out[str(d)] = None if (y == 1995 and str(d) < "1995-03-01") else round(v, 1)  # inject a data gap
    return out


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(openmeteo, "fetch_daily_tmax", fake_fetch)
    monkeypatch.setattr(settings, "heavy_rate_limit_per_minute", 1000)
    from app.db import get_db
    db = get_db()
    for c in ("daily_tmax", "analyses", "geocode"):
        db[c].delete_many({})
    calls["n"] = 0
    return TestClient(main.app)


Q = "/api/analysis?lat=28.6&lon=77.2&start=1990&end=2020&name=Delhi"


def test_analysis_shape_and_caching(client):
    r = client.get(Q)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["meta"]["cached"] is False and len(j["yearly"]) == 31
    assert j["region"]["cell"] == {"lat": 28.5, "lon": 77.25}
    assert j["insights"]["headline"] and len(j["threshold_by_day"]) == 366
    n_upstream = calls["n"]
    j2 = client.get(Q).json()
    assert j2["meta"]["cached"] is True and calls["n"] == n_upstream  # served from MongoDB
    # a different period on the same cell reuses stored raw data: no new upstream call
    client.get("/api/analysis?lat=28.6&lon=77.2&start=2000&end=2015")
    assert calls["n"] == n_upstream


def test_gap_year_flagged_unreliable(client):
    j = client.get(Q).json()
    y95 = next(y for y in j["yearly"] if y["year"] == 1995)
    assert y95["reliable"] is False and 1995 in j["data_quality"]["unreliable_years"]


@pytest.mark.parametrize("qs", [
    "lat=999&lon=0", "lat=10&lon=10&start=2020&end=1990", "lat=10&lon=10&start=1800",
    "lat=10&lon=10&hazard=flood", "south=0&west=0&north=40&east=40",  # box too big
    "south=0&west=0", "lat=abc&lon=1", "lat=10&lon=10&name=<script>alert(1)</script>",
    "lat=10&lon=10&start=1990&end=1992",  # < 5 years
])
def test_bad_input_rejected(client, qs):
    assert client.get("/api/analysis?" + qs).status_code == 422


def test_bbox_uses_centre(client):
    j = client.get("/api/analysis?south=27&west=76&north=29&east=78&start=1995&end=2015").json()
    assert j["region"]["lat"] == 28 and j["region"]["lon"] == 77


def test_csv_export_and_injection_guard(client):
    r = client.get("/api/export.csv?lat=28.6&lon=77.2&name=Delhi")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "year,events,heatwave_days" in r.text
    assert main._csv_safe("=HYPERLINK(1)").startswith("'")


def test_rate_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 3)
    main.limiter._hits.clear()
    codes = [client.get("/api/config").status_code for _ in range(5)]
    assert codes[:3] == [200] * 3 and codes[3:] == [429, 429]


def test_security_headers(client):
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff" and "frame-ancestors" in r.headers["content-security-policy"]


def test_upstream_failure_is_502(client, monkeypatch):
    def boom(*a):
        raise openmeteo.UpstreamError("down")
    monkeypatch.setattr(openmeteo, "fetch_daily_tmax", boom)
    assert client.get("/api/analysis?lat=1&lon=1").status_code == 502
