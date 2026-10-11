"""Trend statistics: OLS regression, Mann-Kendall test with Sen's slope, rolling means."""
from __future__ import annotations

import math
from typing import List, Optional, Sequence

import numpy as np
from scipy import stats

MIN_YEARS_FOR_INFERENCE = 10
ALPHA = 0.05


def rolling_mean(values: Sequence[Optional[float]], window: int = 5) -> List[Optional[float]]:
    """Trailing mean over `window` years; None until a full window of valid years exists."""
    out: List[Optional[float]] = []
    for i in range(len(values)):
        chunk = values[max(0, i - window + 1): i + 1]
        if i + 1 < window or any(v is None for v in chunk):
            out.append(None)
        else:
            out.append(round(float(np.mean(chunk)), 3))
    return out


def mann_kendall(y: np.ndarray) -> dict:
    """Two-sided Mann-Kendall with tie correction (normal approximation, continuity corrected)."""
    n = len(y)
    if n < 4:
        return {"tau": None, "z": None, "p": None, "trend": "insufficient data"}
    diffs = np.sign(y[None, :] - y[:, None])
    s = float(np.triu(diffs, k=1).sum())
    _, counts = np.unique(y, return_counts=True)
    var = (n * (n - 1) * (2 * n + 5) - float(np.sum(counts * (counts - 1) * (2 * counts + 5)))) / 18.0
    if var <= 0:
        return {"tau": 0.0, "z": 0.0, "p": 1.0, "trend": "no trend"}
    z = (s - 1) / math.sqrt(var) if s > 0 else (s + 1) / math.sqrt(var) if s < 0 else 0.0
    p = math.erfc(abs(z) / math.sqrt(2))
    tau = s / (n * (n - 1) / 2)
    trend = "no trend" if p >= ALPHA else ("increasing" if z > 0 else "decreasing")
    return {"tau": round(tau, 3), "z": round(z, 3), "p": round(p, 4), "trend": trend}


def sen_slope(x: np.ndarray, y: np.ndarray) -> Optional[float]:
    if len(x) < 2:
        return None
    i, j = np.triu_indices(len(x), k=1)
    return float(np.median((y[j] - y[i]) / (x[j] - x[i])))


def analyse_metric(years: List[int], values: List[Optional[float]], unit_label: str) -> dict:
    """Trend summary for one yearly metric. Years with value None (unreliable) are dropped."""
    pairs = [(y, v) for y, v in zip(years, values) if v is not None]
    n = len(pairs)
    res: dict = {"n_years": n, "unit": unit_label, "enough_data": n >= MIN_YEARS_FOR_INFERENCE}
    if n < 4:
        return res
    x = np.array([p[0] for p in pairs], dtype=float)
    y = np.array([p[1] for p in pairs], dtype=float)

    lr = stats.linregress(x, y) if np.ptp(y) > 0 else None
    mk = mann_kendall(y)
    sen = sen_slope(x, y)

    k = max(3, min(10, n // 3))
    first, last = float(np.mean(y[:k])), float(np.mean(y[-k:]))
    pct = round((last - first) / first * 100, 1) if first > 0 else None

    res.update({
        "ols_slope_per_decade": round(float(lr.slope) * 10, 3) if lr else 0.0,
        "ols_p": round(float(lr.pvalue), 4) if lr else 1.0,
        "ols_r2": round(float(lr.rvalue ** 2), 3) if lr else 0.0,
        "mann_kendall": mk,
        "sen_slope_per_decade": round(sen * 10, 3) if sen is not None else None,
        "first_period": {"years": [int(x[0]), int(x[k - 1])], "mean": round(first, 2)},
        "last_period": {"years": [int(x[-k]), int(x[-1])], "mean": round(last, 2)},
        "pct_change": pct,
        # significance is claimed only when both tests agree and the record is long enough
        "significant": bool(n >= MIN_YEARS_FOR_INFERENCE and mk["p"] is not None and mk["p"] < ALPHA
                            and lr is not None and lr.pvalue < ALPHA),
    })
    return res
