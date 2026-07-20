"""seasonality_ftest.py -- Erin's nitrate/violation seasonality F-test, ported to the refactored
data layer (src.data.access instead of the old `data` module).

Tests the hypotheses:
H0: after accounting for rainfall, nitrate/violations have NO remaining seasonal structure.
HA: significant seasonal structure remains.

Fits a restricted (rainfall-only) and full (rainfall +
Fourier harmonics) OLS on the day-of-year profile, compares them with a standard F-test and a
HAC-robust Wald test (residuals are autocorrelated -> HAC is the one to trust), and draws the
4-panel diagnostic.

Notebook use:
    from seasonality_ftest import plot_ftest, run_pipeline, make_multi_site_df
    fig = plot_ftest()                    # all sites, K=2; returns the 4-panel figure
    out = run_pipeline(make_multi_site_df(demo_sites), K=2)   # full results dict + figure
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root on path

import warnings

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import statsmodels.formula.api as smf
from statsmodels.stats.anova import anova_lm
from statsmodels.stats.stattools import durbin_watson

from src.data import access


# Build the per-site daily dataframe (adapted to src.data.access)
def make_site_df(site):
    """One row per day for a site: area-weightable nitrate mean/max, violation flag, basin precip.
    Uses get_water / get_weather / get_basin_area directly (no full SiteData build)."""
    precip = access.get_weather(site).groupby("date", as_index=False)["precip_in_1d"].mean()
    water = (
        access.get_water(site)
        .resample("1D")["nitrate_con"]
        .agg(nitrate_con="mean", nitrate_max="max")
        .reset_index(names="date")
    )
    for frame in (water, precip):  # strip any tz so the merge lines up
        frame["date"] = pd.to_datetime(frame["date"])
        if frame["date"].dt.tz is not None:
            frame["date"] = frame["date"].dt.tz_localize(None)
    water["violation"] = (water["nitrate_max"] > 10).astype(int)

    df = water.merge(precip, on="date", how="left")
    df["site_id"] = site
    df["basin_area"] = access.get_basin_area(site)
    return df


def make_multi_site_df(sites):
    return pd.concat([make_site_df(s) for s in sites], ignore_index=True)


# aggregate to one row per date
def build_daily_df(site_df):
    """nitrate_con: basin-area-weighted mean; violation_rate: fraction of sites; precip: site mean."""
    site_df = site_df.copy()
    site_df["date"] = pd.to_datetime(site_df["date"])

    def wmean(group, col):
        w = group["basin_area"]
        return (group[col] * w).sum() / w.sum()

    records = []
    for date, grp in site_df.groupby("date"):
        records.append(
            {
                "date": date,
                "nitrate_con": wmean(grp, "nitrate_con"),  # area-weighted
                "violation_rate": grp["violation"].mean(),  # fraction of sites
                "violation_count": grp["violation"].sum(),
                "precip": grp["precip_in_1d"].mean(),
                "n_sites": len(grp),
            }
        )
    daily_df = pd.DataFrame(records).sort_values("date").reset_index(drop=True)
    daily_df["doy"] = daily_df["date"].dt.dayofyear
    daily_df["year"] = daily_df["date"].dt.year
    return daily_df


# collapse to the DOY profile (average over years)
def build_doy_df(daily_df):
    """One row per day-of-year (1-365), averaged over all years -> a clean seasonal profile."""
    doy_df = (
        daily_df.groupby("doy")
        .agg(
            nitrate_con=("nitrate_con", "mean"),
            violation_rate=("violation_rate", "mean"),
            violation_count=("violation_count", "mean"),
            precip=("precip", "mean"),
            n_years=("year", "nunique"),
        )
        .reset_index()
        .sort_values("doy")
    )
    eps = 1e-4  # logit-transform the rate for OLS (avoids the [0,1] boundary)
    rate = doy_df["violation_rate"].clip(eps, 1 - eps)
    doy_df["violation_logit"] = np.log(rate / (1 - rate))
    return doy_df


# Fourier seasonal terms
def add_fourier_terms(df, doy_col="doy", K=2):
    """K sine/cosine pairs for the annual cycle (K=2 = one cycle + first harmonic)."""
    df = df.copy()
    for k in range(1, K + 1):
        df[f"sin{k}"] = np.sin(2 * np.pi * k * df[doy_col] / 365)
        df[f"cos{k}"] = np.cos(2 * np.pi * k * df[doy_col] / 365)
    return df


# F-test
def run_ftest(doy_df, response_var, K=2, maxlags=14):
    """Restricted (rainfall) vs full (rainfall + harmonics) OLS; standard F, HAC-robust Wald, DW."""
    df = add_fourier_terms(doy_df, K=K).dropna(subset=[response_var, "precip"])
    harmonic_cols = [f"sin{k}" for k in range(1, K + 1)] + [f"cos{k}" for k in range(1, K + 1)]
    harmonic_str = " + ".join(harmonic_cols)

    mod_r = smf.ols(f"{response_var} ~ precip", data=df).fit()
    mod_f = smf.ols(f"{response_var} ~ precip + {harmonic_str}", data=df).fit()

    dw = durbin_watson(mod_f.resid)
    anova_table = anova_lm(mod_r, mod_f)
    mod_f_hac = mod_f.get_robustcov_results(cov_type="HAC", maxlags=maxlags)
    wald = mod_f_hac.f_test(", ".join(f"{c} = 0" for c in harmonic_cols))

    return {
        "response_var": response_var,
        "K": K,
        "n": len(df),
        "R2_restricted": mod_r.rsquared,
        "R2_full": mod_f.rsquared,
        "R2_gain": mod_f.rsquared - mod_r.rsquared,
        "durbin_watson": dw,
        "F_standard": anova_table["F"].iloc[1],
        "p_standard": anova_table["Pr(>F)"].iloc[1],
        "F_hac": float(wald.fvalue),
        "p_hac": float(wald.pvalue),
        "mod_r": mod_r,
        "mod_f": mod_f,
        "df_fitted": df,
        "harmonic_cols": harmonic_cols,
    }


# print summary
def print_results(res):
    dw = res["durbin_watson"]
    rec = "standard F-test" if 1.7 <= dw <= 2.3 else "HAC-robust F-test <- use this (autocorrelation present)"
    print(f"\n{'=' * 60}\n  F-TEST RESULTS  |  Response: {res['response_var']}\n{'=' * 60}")
    print(f"  n observations    : {res['n']} DOY points")
    print(f"  R2 (rainfall only): {res['R2_restricted']:.4f}")
    print(f"  R2 (+ seasonal)   : {res['R2_full']:.4f}   (+{res['R2_gain']:.4f})")
    print(f"  Durbin-Watson     : {dw:.3f}  -> {rec}")
    print(f"  Standard   F = {res['F_standard']:.3f},  p = {res['p_standard']:.4f}")
    print(f"  HAC-robust F = {res['F_hac']:.3f},  p = {res['p_hac']:.4f}")
    p = res["p_hac"] if not (1.7 <= dw <= 2.3) else res["p_standard"]
    print("  => " + ("REJECT H0: seasonal structure remains after rainfall." if p < 0.05 else "FAIL TO REJECT H0."))


# 4-panel diagnostic
def plot_diagnostics(res_nitrate, res_violation):
    """Top row: DOY profiles with rainfall-only vs +seasonal fits. Bottom row: full-model residuals
    vs DOY with the Durbin-Watson stat. Returns the figure (render inline / fig.savefig(...))."""
    fig = plt.figure(figsize=(14, 9))
    gs = gridspec.GridSpec(2, 2, hspace=0.38, wspace=0.32)

    def _plot_fit(ax, res, label, y_raw_col, y_label, color):
        df, mod_r, mod_f = res["df_fitted"], res["mod_r"], res["mod_f"]
        ax.scatter(df["doy"], df[y_raw_col], s=8, alpha=0.4, color="grey", label="Observed (DOY avg)")
        ax.plot(df["doy"], mod_r.fittedvalues, color="steelblue", lw=1.8, ls="--", label="Rainfall only (H0)")
        ax.plot(df["doy"], mod_f.fittedvalues, color=color, lw=2.2, label=f"+ Seasonal (Ha), R2={res['R2_full']:.3f}")
        p_use = res["p_hac"] if not (1.7 <= res["durbin_watson"] <= 2.3) else res["p_standard"]
        sig = "***" if p_use < 0.001 else ("**" if p_use < 0.01 else ("*" if p_use < 0.05 else "ns"))
        ax.set_title(f"{label}  (F-test p={p_use:.4f} {sig})", fontsize=11)
        ax.set_xlabel("Day of Year")
        ax.set_ylabel(y_label)
        ax.legend(fontsize=8)

    def _plot_resid(ax, res, label, color):
        df = res["df_fitted"]
        ax.scatter(df["doy"], res["mod_f"].resid, s=7, alpha=0.45, color=color)
        ax.axhline(0, color="black", lw=1)
        dw_val = res["durbin_watson"]
        dw_col = "green" if 1.7 <= dw_val <= 2.3 else "red"
        ax.set_title(f"{label} -- full-model residuals\nDurbin-Watson = {dw_val:.3f}", fontsize=10, color=dw_col)
        ax.set_xlabel("Day of Year")
        ax.set_ylabel("Residual")

    _plot_fit(
        fig.add_subplot(gs[0, 0]),
        res_nitrate,
        "Nitrate Concentration",
        "nitrate_con",
        "mg/L (area-wtd mean)",
        "darkorange",
    )
    _plot_fit(
        fig.add_subplot(gs[0, 1]),
        res_violation,
        "Violation Rate (logit)",
        "violation_logit",
        "logit(violation rate)",
        "mediumseagreen",
    )
    _plot_resid(fig.add_subplot(gs[1, 0]), res_nitrate, "Nitrate", "darkorange")
    _plot_resid(fig.add_subplot(gs[1, 1]), res_violation, "Violation", "mediumseagreen")
    fig.suptitle(
        "Seasonal F-Test: Iowa nitrate & violations\nH0: no seasonal structure after accounting for rainfall",
        fontsize=13,
        fontweight="bold",
        y=1.01,
    )
    return fig


# pipeline + one-liner
def run_pipeline(site_df, K=2, verbose=True):
    """Raw per-site frame -> daily -> DOY profile -> F-tests + diagnostic figure."""
    daily_df = build_daily_df(site_df)
    doy_df = build_doy_df(daily_df)
    res_nitrate = run_ftest(doy_df, response_var="nitrate_con", K=K)
    res_violation = run_ftest(doy_df, response_var="violation_logit", K=K)
    if verbose:
        print_results(res_nitrate)
        print_results(res_violation)
    fig = plot_diagnostics(res_nitrate, res_violation)
    plt.close(fig)  # drop it from pyplot's manager so the inline backend doesn't ALSO auto-show it
    return {  # (the caller displays it via the returned value's repr -> exactly one image)
        "daily_df": daily_df,
        "doy_df": doy_df,
        "res_nitrate": res_nitrate,
        "res_violation": res_violation,
        "fig": fig,
    }


def plot_ftest(sites=None, K=2, verbose=True):
    """One-liner for the notebook: build the multi-site frame, run the pipeline, return the figure."""
    sites = sites if sites is not None else access.get_site_ids()
    return run_pipeline(make_multi_site_df(sites), K=K, verbose=verbose)["fig"]
