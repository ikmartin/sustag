"""eda_demo.py, sample of EDA methods used in the EDA phase of the project.

Each function pulls from the live `src.data` access layer and returns a matplotlib (fig, ax) so it renders inline for the notebook. Import and call directly:

Covers the project's most important EDA findings: seasonality, strong autocorrelation, "what's upstream" land use, basin variability/variety, and the classification class imbalance (violation days are rarer than safe days).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root on path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from src.data import access
from src.features.features import daily_nitrate

VIOLATION_MGL = 10.0  # EPA drinking-water limit; the classification target boundary

# CDL crop categories -> colours dictionary
CROP_COLORS = {
    "Corn": "#f2c14e",
    "Soybeans": "#2f7d32",
    "Hay_Pasture": "#8bc34a",
    "Alfalfa": "#7e57c2",
    "Small_Grains": "#c8a15a",
    "Fallow": "#a1887f",
    "Nonag": "#9e9e9e",
    "Other": "#d7ccc8",
}

##############
# site helpers
##############


# return good sites
def demo_sites(min_obs: int = 1500) -> list:
    """Site UIDs with at least `min_obs` observed daily-nitrate days -- the modelling pool."""
    return [s for s in access.get_site_ids() if daily_nitrate(site_uid=s).dropna().shape[0] >= min_obs]


# get max daily nitrate
def _daily(uid: str, agg: str = "max") -> pd.Series:
    s = daily_nitrate(site_uid=uid, agg_meth=agg)
    s.index = pd.to_datetime(s.index)
    return s


def _daily_precip(uid: str) -> pd.Series:
    """Basin-mean daily precipitation (in). get_weather has one row per grid cell per day, so
    average over cells to get the basin-wide daily value (matches the widget)."""
    w = access.get_weather(uid)
    s = w.groupby("date")["precip_in_1d"].mean()  # column is precip_in_1d
    s.index = pd.to_datetime(s.index)
    return s


# display the raw signal
def plot_nitrate_timeseries(uid: str, agg: str = "max"):
    """Daily nitrate over the sensor's life, with the 10 mg/L violation line. Shows the recurring spring/early-summer peaks that motivate the seasonal features."""
    s = _daily(uid, agg)  # nitrate; NaN at gaps -> line breaks
    fig, ax = plt.subplots(figsize=(12, 4))

    # precipitation on a secondary right axis
    ax2 = ax.twinx()
    rain = _daily_precip(uid)
    ax2.bar(rain.index, rain.values, width=1.0, color="#3a94fa", alpha=0.6, label="precip (in)")
    ax2.set_ylabel("precipitation (in)", color="#3a94fa")
    ax2.tick_params(axis="y", labelcolor="#3a94fa")

    # nitrate on the left axis, on top of the precip bars
    ax.set_zorder(ax2.get_zorder() + 1)
    ax.patch.set_visible(False)  # transparent so the precip bars show through
    ax.plot(s.index, s.values, color="#1fb473", lw=1.2, alpha=0.9, label="nitrate", zorder=3)
    ax.axhline(VIOLATION_MGL, ls="--", color="#c1121f", alpha=0.7, label=f"{VIOLATION_MGL:.0f} mg/L (violation)")
    ax.set(title=f"Daily {agg} nitrate + precip — {uid}", xlabel="date", ylabel="nitrate (mg/L)")

    # one combined legend across both axes
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper right", fontsize=9)
    fig.tight_layout()
    return fig, ax


# demonstrate seasonality (day-of-year climatology)
def plot_seasonality(uid: str, agg: str = "max"):
    """Nitrate by day-of-year: faint per-year traces + the mean climatology (red). The spring/
    early-summer peak is the seasonal signal the doy sin/cos features capture."""
    s = _daily(uid, agg)
    doy = s.index.dayofyear.to_numpy()
    year = s.index.year.to_numpy()
    val = s.to_numpy()
    clim = pd.Series(val).groupby(doy).mean()

    fig, ax = plt.subplots(figsize=(11, 4))
    for yr in np.unique(year):
        m = year == yr
        order = np.argsort(doy[m])
        ax.plot(doy[m][order], val[m][order], color="0.8", lw=0.6)
    ax.plot(clim.index, clim.values, color="#c1121f", lw=2.2, label="mean climatology")
    ax.axhline(VIOLATION_MGL, ls="--", color="k", alpha=0.35)
    ax.set(title=f"Seasonality (day-of-year climatology) — {uid}", xlabel="day of year", ylabel=f"{agg} nitrate (mg/L)")
    ax.legend(loc="upper right", fontsize=9)
    fig.tight_layout()
    return fig, ax


# nitrate + rain autocorrelation together (Isaac's autocorrelation_of_rain_sites, one site)
def plot_autocorrelation(uid: str, lags: int = 90, agg: str = "max"):
    """ACF of daily nitrate (orange) and daily rain (blue) out to `lags` days -- one site's version
    of Isaac's autocorrelation_of_rain_sites grid. Nitrate decays slowly (strong memory -> 'predict
    yesterday' is a hard baseline to beat); rain drops off fast. statsmodels plot_acf, with
    missing='conservative' so the daily gaps are handled."""
    import statsmodels.api as sm

    water = _daily(uid, agg)  # daily nitrate (NaN at gaps)
    rain = _daily_precip(uid)  # daily basin-mean precip
    fig, ax = plt.subplots(figsize=(9, 4))
    sm.graphics.tsa.plot_acf(
        water.values,
        lags=lags,
        ax=ax,
        missing="conservative",
        color="tab:orange",
        vlines_kwargs={"colors": "tab:orange"},
        label="nitrate",
    )
    sm.graphics.tsa.plot_acf(
        rain.values,
        lags=lags,
        ax=ax,
        missing="conservative",
        color="tab:blue",
        vlines_kwargs={"colors": "tab:blue"},
        label="rain",
    )
    ax.set(
        title=f"Autocorrelation: nitrate vs rain — {uid}",
        xlabel="lag (days)",
        ylabel="autocorrelation",
        ylim=(-1.1, 1.1),
    )
    ax.legend(fontsize=9)
    fig.tight_layout()
    return fig, ax


# basin cells by dominant crop
def plot_basin_crops(uid: str, year: int = 2017):
    """The site's drainage basin, tiled by the weather-grid cells, each coloured by its dominant crop for `year`, with the sensor starred at the outlet. Just a display, also visible in the widget."""
    grid = access.get_grid(uid)
    basin = access.get_basin(uid).to_crs(grid.crs)
    lon, lat = access.get_location(uid)
    import geopandas as gpd
    from shapely.geometry import Point

    sensor = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(grid.crs).iloc[0]

    crops = access.get_crops(uid)
    yr = crops[crops["year"] == year] if year in set(crops["year"]) else crops
    cols = [c for c in CROP_COLORS if c in yr.columns]
    dom = yr.groupby("node_id")[cols].sum()
    dom = dom.idxmax(axis=1).where(dom.sum(axis=1) > 0)
    g = grid.merge(dom.rename("dom").reset_index(), on="node_id", how="left")

    fig, ax = plt.subplots(figsize=(7.5, 7.2))
    for crop in cols:
        sub = g[g["dom"] == crop]
        if len(sub):
            sub.plot(ax=ax, color=CROP_COLORS[crop], edgecolor="white", linewidth=0.4, alpha=0.92, aspect=1)
    na = g[g["dom"].isna()]
    if len(na):
        na.plot(ax=ax, color="#eceff1", edgecolor="white", linewidth=0.4, aspect=1)
    basin.boundary.plot(ax=ax, color="#1f2933", linewidth=2.4, aspect=1)
    ax.scatter([sensor.x], [sensor.y], marker="*", s=520, color="#c1121f", edgecolor="white", linewidth=1.4, zorder=6)

    handles = [
        Line2D([0], [0], marker="s", ls="", ms=10, mfc=CROP_COLORS[c], mec="white", label=c.replace("_", " "))
        for c in cols
    ]
    handles += [
        Line2D([0], [0], marker="*", ls="", ms=14, mfc="#c1121f", mec="white", label="sensor"),
        Line2D([0], [0], color="#1f2933", lw=2.4, label="basin"),
    ]
    ax.legend(handles=handles, loc="upper left", fontsize=9, frameon=True, framealpha=0.95)
    ax.set_title(f"What drains to {uid}: cells by dominant crop ({year})", fontsize=12)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.tight_layout()
    return fig, ax


# basin-scale variability
def plot_basin_size_distribution(sites=None, bins: int = 30):
    """Histogram of basin area (km²) across the fleet -- the heavy right-skew that makes cross-site transfer harder and means basin-area should be a feature"""
    sites = sites or access.get_site_ids()
    areas = []
    for s in sites:
        try:
            areas.append(access.get_basin_area(s) / 1e6)  # m² -> km²
        except Exception:
            pass
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.hist(areas, bins=bins, color="#4c78a8", edgecolor="white")
    ax.set(title=f"Basin area distribution ({len(areas)} sites)", xlabel="basin area (km²)", ylabel="# sites")
    fig.tight_layout()
    return fig, ax


# class imbalance (violation rate)
def plot_violation_balance(sites=None, agg: str = "max", min_days: int = 30):
    """Per-site violation rate = fraction of days with nitrate >= 10 mg/L. Shows the classification class imbalance and its wide spread across sites. Illustrates that that a single global threshold is a compromise, but a necessary one since site-specific imbalance tuning requires nitrate history which is impossible for a virtual site."""
    sites = sites or access.get_site_ids()
    rates = []
    for s in sites:
        d = _daily(s, agg)
        if len(d) >= min_days:
            rates.append(float((d >= VIOLATION_MGL).mean()))
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.hist(rates, bins=20, color="#e45756", edgecolor="white")
    ax.axvline(np.mean(rates), color="k", ls="--", label=f"fleet mean {np.mean(rates):.0%}")
    ax.set(
        title=f"Per-site violation rate ({len(rates)} sites) — class balance",
        xlabel="fraction of days ≥ 10 mg/L",
        ylabel="# sites",
    )
    ax.legend(fontsize=9)
    fig.tight_layout()
    return fig, ax


# fleet-wide seasonality: every site's day-of-year climatology + their average (the all-sites version)
def plot_fleet_seasonality(sites=None, agg: str = "max", min_days: int = 60):
    """Average seasonality across ALL sites: each site's day-of-year climatology (faint grey) plus the fleet mean (bold red) -- the shared spring/early-summer peak is the seasonal signal the doy features capture. The all-sites version of plot_seasonality. (nitrate_avg_seasonal() gives the same cross-site cycle as one Series if you want it without the per-site spread.)"""
    sites = sites or access.get_site_ids()
    clims = {}
    for s in sites:
        d = _daily(s, agg).dropna()
        if len(d) >= min_days:
            clims[s] = d.groupby(d.index.dayofyear).mean()
    clim_df = pd.DataFrame(clims)  # index = day-of-year (1-366), columns = sites
    fleet = clim_df.mean(axis=1)  # equal-weight average across sites

    fig, ax = plt.subplots(figsize=(11, 4))
    for col in clim_df.columns:
        ax.plot(clim_df.index, clim_df[col].to_numpy(), color="0.85", lw=0.5)
    ax.plot(fleet.index, fleet.to_numpy(), color="#c1121f", lw=2.4, label=f"fleet mean ({clim_df.shape[1]} sites)")
    ax.axhline(VIOLATION_MGL, ls="--", color="k", alpha=0.35)
    ax.set(
        title="Fleet seasonality (day-of-year climatology, all sites)",
        xlabel="day of year",
        ylabel=f"mean {agg} nitrate (mg/L)",
    )
    ax.legend(loc="upper right", fontsize=9)
    fig.tight_layout()
    return fig, ax


# Isaac's violation seasonality: avg # of violation days per month, per site + fleet (cell_importance.ipynb)
def _monthly_violation_days(uid: str) -> pd.DataFrame:
    """Avg number of violation days per calendar month for one site (Isaac's
    get_mean_month_violation_number): daily-max nitrate -> violation flag (>= 10 mg/L) -> monthly SUM
    (# violation days that month) -> average across years by calendar month. Index = month (1-12)."""
    w = pd.DataFrame(access.get_water(uid)["nitrate_con"].resample("1D").max())
    w["violation"] = w["nitrate_con"] >= VIOLATION_MGL
    m = w.resample("1MS").agg({"nitrate_con": "max", "violation": "sum"}).reset_index()
    m["month"] = m["datetime"].dt.month
    return m[["month", "violation"]].groupby("month").mean()


def plot_violation_days_by_month(sites=None):
    """Isaac's two-panel violation-seasonality figure (cell_importance.ipynb)."""
    sites = sites or access.get_site_ids()
    frames = []
    for s in sites:
        try:
            frames.append(_monthly_violation_days(s))
        except Exception:
            pass
    total = pd.concat(frames).groupby(level="month")["violation"].sum()  # summed across sites per month

    fig, axes = plt.subplots(2, 1, figsize=(12, 10))
    for frame in frames:
        axes[0].plot(frame.index, frame["violation"].to_numpy(), lw=0.7)
    axes[1].plot(total.index, (total / len(frames)).to_numpy(), color="#c1121f", lw=2.4)
    axes[0].set_title("Avg # Violation Days Every Site")
    axes[1].set_title("Avg # Days with Violation by Month Across All Sites")
    for ax in axes:
        ax.set_xlabel("month")
        ax.set_ylabel("avg # violation days")
        ax.set_xticks(range(1, 13))
    fig.tight_layout()
    return fig, axes
