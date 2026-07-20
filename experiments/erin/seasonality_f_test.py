"""
Nitrogen Violation Seasonality F-Test Pipeline
===============================================
Tests H0: After accounting for rainfall, nitrogen violations have no remaining
          seasonal structure.
HA: After accounting for rainfall, significant seasonal structure remains.

Input dataframes expected:
  - site_df   : date, site_id, violation (0/1), nitrate_con, precip_in_1d
  - basin_df  : site_id, basin_area

Output:
  - DOY-averaged analysis dataframe
  - F-test results (standard + HAC-robust) for both nitrate_con and violation_rate
  - Diagnostic plots
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from scipy import stats
import statsmodels.formula.api as smf
from statsmodels.stats.anova import anova_lm
from statsmodels.stats.stattools import durbin_watson
import warnings

warnings.filterwarnings("ignore")
import sys

sys.path.insert(0, "../")  # replace with path/to/project/root
from data import get_data, get_site_ids
from data.water import aggregate_by_interval

# -- Section 0: Build dataframes from get_data and get_site_ids


def make_site_df(site):

    data = get_data(site)

    # Daily precipitation
    precip = data.rain.groupby("date", as_index=False)["precip_in_1d"].mean()

    # Daily nitrate statistics
    water = (
        data.water.resample("1D")["nitrate_con"]
        .agg(
            nitrate_con="mean",
            nitrate_max="max",
        )
        .reset_index(names="date")
        .assign(
            date=lambda x: x["date"].dt.tz_localize(None),
            violation=lambda x: (x["nitrate_max"] > 10).astype(int),
        )
    )

    # Base dataframe
    df = water.merge(precip, on="date", how="left")

    # Site-level features
    df["site_id"] = site
    df["basin_area"] = data.basin_area

    return df


def make_multi_site_df(sites):

    return pd.concat(
        [make_site_df(site) for site in sites],
        ignore_index=True,
    )


# ── SECTION 1: Merge basin area and aggregate to daily ────────────────────────


def build_daily_df(site_df):
    """
    Aggregate site-level data to one row per date.

    - nitrate_con  : weighted mean by basin_area (larger basins = more load)
    - violation    : unweighted mean (fraction of sites violating) + raw count
    - precip       : unweighted mean across all sites for that date
    """
    site_df = site_df.copy()
    site_df["date"] = pd.to_datetime(site_df["date"])

    # Weighted mean helper
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
                "violation_count": grp["violation"].sum(),  # raw count
                "precip": grp["precip_in_1d"].mean(),  # unweighted site avg
                "n_sites": len(grp),
            }
        )

    daily_df = pd.DataFrame(records).sort_values("date").reset_index(drop=True)
    daily_df["doy"] = daily_df["date"].dt.dayofyear
    daily_df["year"] = daily_df["date"].dt.year

    return daily_df


# ── SECTION 2: Collapse to DOY profile (average over years) ──────────────────


def build_doy_df(daily_df):
    """
    Collapse daily data to a single annual cycle: one row per DOY (1–365).
    Averages over all years, giving a clean 365-point seasonal profile.
    """
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

    # Logit-transform violation_rate for OLS (avoids [0,1] boundary issues)
    # Clip to avoid log(0); any rate of exactly 0 or 1 gets nudged slightly
    eps = 1e-4
    rate = doy_df["violation_rate"].clip(eps, 1 - eps)
    doy_df["violation_logit"] = np.log(rate / (1 - rate))

    return doy_df


# ── SECTION 3: Add Fourier seasonal terms ────────────────────────────────────


def add_fourier_terms(df, doy_col="doy", K=2):
    """
    Add K sine/cosine pairs capturing the annual cycle.
    K=2 covers one full cycle + first harmonic (recommended starting point).
    K=3 adds a second harmonic for more complex shapes.
    """
    df = df.copy()
    for k in range(1, K + 1):
        df[f"sin{k}"] = np.sin(2 * np.pi * k * df[doy_col] / 365)
        df[f"cos{k}"] = np.cos(2 * np.pi * k * df[doy_col] / 365)
    return df


# ── SECTION 4: Run F-test (standard + HAC-robust) ────────────────────────────


def run_ftest(doy_df, response_var, K=2, maxlags=14):
    """
    Fits restricted (rainfall only) and full (rainfall + seasonal harmonics)
    models, then reports both standard and HAC-robust F-tests.

    Parameters
    ----------
    response_var : str
        Column name to use as the dependent variable.
        Recommended: 'nitrate_con' or 'violation_logit'
    K : int
        Number of Fourier harmonics (default 2)
    maxlags : int
        Newey-West lag truncation for HAC standard errors.
        Rule of thumb: int(4 * (n/100)^(2/9)) or simply 14 for n=365.
    """
    df = add_fourier_terms(doy_df, K=K).dropna(subset=[response_var, "precip"])

    harmonic_cols = [f"sin{k}" for k in range(1, K + 1)] + [f"cos{k}" for k in range(1, K + 1)]
    harmonic_str = " + ".join(harmonic_cols)

    formula_r = f"{response_var} ~ precip"
    formula_f = f"{response_var} ~ precip + {harmonic_str}"

    mod_r = smf.ols(formula_r, data=df).fit()
    mod_f = smf.ols(formula_f, data=df).fit()

    # ── Durbin-Watson on full model residuals ──────────────────────────────
    dw = durbin_watson(mod_f.resid)

    # ── Standard F-test ───────────────────────────────────────────────────
    anova_table = anova_lm(mod_r, mod_f)

    # ── HAC-robust Wald test on seasonal coefficients ─────────────────────
    mod_f_hac = mod_f.get_robustcov_results(cov_type="HAC", maxlags=maxlags)
    restrictions = ", ".join([f"{c} = 0" for c in harmonic_cols])
    wald = mod_f_hac.f_test(restrictions)

    # ── R² improvement ────────────────────────────────────────────────────
    r2_restricted = mod_r.rsquared
    r2_full = mod_f.rsquared
    r2_gain = r2_full - r2_restricted

    results = {
        "response_var": response_var,
        "K": K,
        "n": len(df),
        "R2_restricted": r2_restricted,
        "R2_full": r2_full,
        "R2_gain": r2_gain,
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
    return results


# ── SECTION 5: Print summary ──────────────────────────────────────────────────


def print_results(res):
    dw = res["durbin_watson"]
    rec = "standard F-test" if 1.7 <= dw <= 2.3 else "HAC-robust F-test ← use this (autocorrelation present)"

    print(f"\n{'='*60}")
    print(f"  F-TEST RESULTS  |  Response: {res['response_var']}")
    print(f"{'='*60}")
    print(f"  n observations    : {res['n']} DOY points")
    print(f"  Fourier harmonics : K = {res['K']}")
    print(f"  R² (rainfall only): {res['R2_restricted']:.4f}")
    print(f"  R² (+ seasonal)   : {res['R2_full']:.4f}")
    print(f"  R² improvement    : +{res['R2_gain']:.4f}")
    print(f"\n  Durbin-Watson     : {dw:.3f}  (2.0 = no autocorrelation)")
    print(f"  → Recommended test: {rec}")
    print(
        f"\n  Standard   F({2*res['K']}, {res['n']-2*res['K']-2}) = "
        f"{res['F_standard']:.3f},   p = {res['p_standard']:.4f}"
    )
    print(f"  HAC-robust F({2*res['K']}, {res['n']-2*res['K']-2}) = " f"{res['F_hac']:.3f},   p = {res['p_hac']:.4f}")
    print()

    p = res["p_hac"] if not (1.7 <= dw <= 2.3) else res["p_standard"]
    if p < 0.05:
        print("  ✓ REJECT H0: Significant seasonal structure remains after")
        print("    accounting for rainfall.")
    else:
        print("  ✗ FAIL TO REJECT H0: No significant seasonal structure")
        print("    beyond what rainfall explains.")
    print(f"{'='*60}\n")


# ── SECTION 6: Diagnostic plots ──────────────────────────────────────────────


def plot_diagnostics(res_nitrate, res_violation, save_path="ftest_diagnostics.png"):
    """
    4-panel figure:
      Top-left  : DOY profile of nitrate_con with model fits
      Top-right : DOY profile of violation_rate with model fits
      Bot-left  : Residuals of full nitrate model vs DOY
      Bot-right : Residuals of full violation model vs DOY
    """
    fig = plt.figure(figsize=(14, 9))
    gs = gridspec.GridSpec(2, 2, hspace=0.38, wspace=0.32)

    def _plot_fit(ax, res, label, y_raw_col, y_label, color):
        df = res["df_fitted"]
        mod_r = res["mod_r"]
        mod_f = res["mod_f"]

        ax.scatter(df["doy"], df[y_raw_col], s=8, alpha=0.4, color="grey", label="Observed (DOY avg)")
        ax.plot(df["doy"], mod_r.fittedvalues, color="steelblue", lw=1.8, linestyle="--", label="Rainfall only (H₀)")
        ax.plot(df["doy"], mod_f.fittedvalues, color=color, lw=2.2, label=f"+ Seasonal (Hₐ), R²={res['R2_full']:.3f}")

        p_use = res["p_hac"] if res["durbin_watson"] < 1.7 or res["durbin_watson"] > 2.3 else res["p_standard"]
        sig = "***" if p_use < 0.001 else ("**" if p_use < 0.01 else ("*" if p_use < 0.05 else "ns"))
        ax.set_title(f"{label}  (F-test p={p_use:.4f} {sig})", fontsize=11)
        ax.set_xlabel("Day of Year")
        ax.set_ylabel(y_label)
        ax.legend(fontsize=8)

    def _plot_resid(ax, res, label, color):
        df = res["df_fitted"]
        resid = res["mod_f"].resid
        ax.scatter(df["doy"], resid, s=7, alpha=0.45, color=color)
        ax.axhline(0, color="black", lw=1)
        dw_val = res["durbin_watson"]
        dw_col = "green" if 1.7 <= dw_val <= 2.3 else "red"
        ax.set_title(f"{label} — Full model residuals\n" f"Durbin-Watson = {dw_val:.3f}", fontsize=10, color=dw_col)
        ax.set_xlabel("Day of Year")
        ax.set_ylabel("Residual")

    ax1 = fig.add_subplot(gs[0, 0])
    ax2 = fig.add_subplot(gs[0, 1])
    ax3 = fig.add_subplot(gs[1, 0])
    ax4 = fig.add_subplot(gs[1, 1])

    _plot_fit(ax1, res_nitrate, "Nitrate Concentration", "nitrate_con", "mg/L (area-wtd mean)", "darkorange")
    _plot_fit(
        ax2, res_violation, "Violation Rate (logit)", "violation_logit", "logit(violation rate)", "mediumseagreen"
    )
    _plot_resid(ax3, res_nitrate, "Nitrate", "darkorange")
    _plot_resid(ax4, res_violation, "Violation", "mediumseagreen")

    fig.suptitle(
        "Seasonal F-Test: Nitrogen Violations in Iowa\n" "H₀: No seasonal structure after accounting for rainfall",
        fontsize=13,
        fontweight="bold",
        y=1.01,
    )
    plt.savefig(save_path, dpi=150, bbox_inches="tight")
    print(f"  Diagnostic plot saved to: {save_path}")
    plt.close()


# ── MAIN ──────────────────────────────────────────────────────────────────────


def run_pipeline(site_df, K=2):
    """
    Full pipeline from raw inputs to F-test results and diagnostic plots.

    Parameters
    ----------
    site_df  : DataFrame with columns [date, site_id, violation, nitrate_con,
                                        precip_in_1d, basin_area]
    K        : Number of Fourier harmonics (start with 2, try 3 if residuals
               show remaining seasonal structure)
    """
    print("Step 1: Aggregating to daily...")
    daily_df = build_daily_df(site_df)
    print(
        f"  → {len(daily_df)} daily records, "
        f"{daily_df['year'].nunique()} years, "
        f"{daily_df['n_sites'].mean():.1f} avg sites/day"
    )

    print("\nStep 2: Collapsing to DOY profile (averaging over years)...")
    doy_df = build_doy_df(daily_df)
    print(f"  → {len(doy_df)} DOY rows, " f"{doy_df['n_years'].mean():.1f} avg years per DOY")

    print(f"\nStep 3: Running F-tests with K={K} Fourier harmonics...")

    res_nitrate = run_ftest(doy_df, response_var="nitrate_con", K=K)
    res_violation = run_ftest(doy_df, response_var="violation_logit", K=K)

    print_results(res_nitrate)
    print_results(res_violation)

    print("Step 4: Generating diagnostic plots...")
    plot_diagnostics(res_nitrate, res_violation, save_path="ftest_diagnostics.png")

    return {
        "daily_df": daily_df,
        "doy_df": doy_df,
        "res_nitrate": res_nitrate,
        "res_violation": res_violation,
    }
