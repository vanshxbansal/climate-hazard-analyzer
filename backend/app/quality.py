"""Cleaning for noisy daily temperature series: physical bounds, spikes, gaps."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

import numpy as np

TMAX_MIN, TMAX_MAX = -90.0, 60.0  # beyond any recorded surface air temperature
SPIKE_C = 15.0  # |x - local median| above this is treated as a sensor/processing glitch
MIN_YEAR_COVERAGE = 0.90  # years with less valid data are excluded from trend statistics


@dataclass
class QualityReport:
    total_days: int = 0
    missing_days: int = 0
    out_of_range: int = 0
    spikes: int = 0
    unreliable_years: list = field(default_factory=list)

    def as_dict(self) -> Dict:
        return {
            "total_days": self.total_days,
            "missing_days": self.missing_days,
            "out_of_range_removed": self.out_of_range,
            "spikes_removed": self.spikes,
            "unreliable_years": self.unreliable_years,
            "valid_fraction": round(1 - (self.missing_days + self.out_of_range + self.spikes) / max(self.total_days, 1), 4),
        }


def _rolling_median(x: np.ndarray, w: int = 7) -> np.ndarray:
    pad = w // 2
    xp = np.pad(x, pad, constant_values=np.nan)
    windows = np.lib.stride_tricks.sliding_window_view(xp, w)
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            return np.nanmedian(windows, axis=1)


def clean(tmax: np.ndarray, report: QualityReport) -> np.ndarray:
    """Return a copy with invalid values set to NaN; counts are accumulated in report."""
    x = tmax.astype(float).copy()
    report.total_days += x.size
    report.missing_days += int(np.isnan(x).sum())

    bad = (x < TMAX_MIN) | (x > TMAX_MAX)
    report.out_of_range += int(bad.sum())
    x[bad] = np.nan

    med = _rolling_median(x)
    with np.errstate(invalid="ignore"):
        spike = np.abs(x - med) > SPIKE_C
    report.spikes += int(spike.sum())
    x[spike] = np.nan
    return x
