"""Turn trend statistics into plain-language findings, without overclaiming."""
from __future__ import annotations

from typing import Dict, List


def _fmt_period(p: dict) -> str:
    a, b = p["years"]
    return f"{a}-{b}"


def build(region_name: str, trends: Dict[str, dict], baseline: dict, years: List[int], quality: dict) -> dict:
    days = trends["hw_days"]
    ev = trends["events"]
    bullets: List[dict] = []

    if not days.get("enough_data"):
        headline = (f"Only {days.get('n_years', 0)} usable years: too few to say anything reliable about a trend "
                    f"(at least 10 needed).")
        bullets.append({"level": "warn", "text": "Select a longer period for trend inference."})
        return {"headline": headline, "bullets": bullets}

    f, l, pct = days["first_period"], days["last_period"], days["pct_change"]
    mk = days["mann_kendall"]
    if pct is None:
        change = f"from none to {l['mean']} heatwave days per year" if l["mean"] > 0 else "no heatwave days in either period"
    else:
        verb = "increased" if pct > 0 else "decreased" if pct < 0 else "did not change"
        change = f"{verb} {abs(pct):.0f}% ({f['mean']} to {l['mean']} days/yr)"

    if days["significant"]:
        direction = "upward" if mk["trend"] == "increasing" else "downward"
        stat = f"a statistically significant {direction} trend (Mann-Kendall p={mk['p']}, OLS p={days['ols_p']})"
    else:
        stat = (f"no statistically significant trend (Mann-Kendall p={mk['p']}, OLS p={days['ols_p']}), "
                f"so this change may be natural year-to-year variability")
    headline = (f"In {region_name}, heatwave days per year {change} between {_fmt_period(f)} and "
                f"{_fmt_period(l)}: {stat}.")

    bullets.append({"level": "info", "text":
        f"Linear trend: {days['ols_slope_per_decade']:+} heatwave days per decade "
        f"(Sen's slope {days['sen_slope_per_decade']:+}, R²={days['ols_r2']})."})
    if ev.get("enough_data"):
        e = ev["pct_change"]
        bullets.append({"level": "info", "text":
            f"Heatwave events per year: {ev['first_period']['mean']} → {ev['last_period']['mean']}"
            + (f" ({e:+.0f}%)" if e is not None else "") + f"; trend {ev['mann_kendall']['trend']}."})
    d = trends.get("mean_duration", {})
    if d.get("enough_data"):
        bullets.append({"level": "info", "text":
            f"Average event length: {d['first_period']['mean']} → {d['last_period']['mean']} days; "
            f"trend {d['mann_kendall']['trend']}."})
    i = trends.get("mean_intensity", {})
    if i.get("enough_data"):
        bullets.append({"level": "info", "text":
            f"Mean intensity (°C above the 95th percentile on heatwave days): "
            f"{i['first_period']['mean']} → {i['last_period']['mean']}; trend {i['mann_kendall']['trend']}."})

    # honesty section
    bs, be = baseline["start"], baseline["end"]
    overlap = sum(1 for y in years if bs <= y <= be)
    if years and overlap / len(years) >= 0.5:
        bullets.append({"level": "warn", "text":
            f"{overlap} of the {len(years)} selected years lie inside the baseline ({bs}-{be}), so the average "
            f"exceedance rate is ~5% by construction; the change over time is informative, the absolute count is not."})
    elif years and years[-1] < bs:
        bullets.append({"level": "warn", "text":
            f"The period precedes the baseline ({bs}-{be}); counts are relative to a warmer climate "
            f"and will look low."})
    if quality["unreliable_years"]:
        bullets.append({"level": "warn", "text":
            f"{len(quality['unreliable_years'])} year(s) with <90% valid data were excluded from the trend "
            f"statistics: {', '.join(map(str, quality['unreliable_years'][:8]))}."})
    bullets.append({"level": "warn", "text":
        "Data are ERA5 reanalysis for a single ~25 km grid cell, not station records; urban heat islands, "
        "local extremes and station inhomogeneities are not captured. Short records are sensitive to a few "
        "extreme summers, and serial correlation is not modelled."})
    return {"headline": headline, "bullets": bullets}
