"""Tier 2's remaining battery: does a source say anything the others do not, and does it say it over time?

Three questions, none of which asks about predictive power -- that is a property of a task, not of the data.

**Incremental information.** A covariate earns its place by what it adds, not by what it correlates with. Pairwise correlation cannot answer that: two products can each correlate weakly with a third and still be jointly redundant with it. So the measure here is the R2 of each product regressed on ALL the others -- high means the product is reconstructible from what we already hold, and its apparent signal is already in the pool.

**Temporal decomposition.** An annual product is not automatically a time series. Its variance splits into a between-catchment part (a static map), a between-year part (a national trend), and the interaction -- and only the interaction is spatiotemporal information. A product whose interaction share is near zero is a static map plus a year dummy, which is worth knowing before a model spends 18 years of rows on it.

**Missingness.** Whether absence is random or structured, since `absent is not zero` only protects the join -- it does not tell a model that the gap is informative.

Everything samples catchments rather than reading the full product: nutrients alone is 320.8M rows, and these are distributional questions where a few tens of thousands of catchments settle the answer.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SAMPLE_COMIDS = 20_000
_SEED = 20260827


def sample_comids(n: int = SAMPLE_COMIDS, seed: int = _SEED) -> np.ndarray:
    """A stable random catchment sample. Seeded, so a regenerated report does not move for sampling reasons alone."""
    from data.access import api

    vaa = api.get_network("vaa")
    ids = pd.to_numeric(vaa.comid, errors="coerce").dropna().astype("int64").to_numpy()
    rng = np.random.default_rng(seed)
    return np.sort(rng.choice(ids, size=min(n, len(ids)), replace=False))


def nutrients_wide(comids=None) -> pd.DataFrame:
    """(comid, year) x product of nutrient kg, read with the comid filter pushed into the parquet."""
    import pyarrow.parquet as pq

    from data.access import config as acfg

    comids = sample_comids() if comids is None else np.asarray(comids)
    p = acfg.PUB_COMID_FEATURES / "nutrients.parquet"
    t = pq.read_table(p, columns=["comid", "year", "product", "kg"],
                      filters=[("comid", "in", set(int(c) for c in comids))])
    d = t.to_pandas()
    return d.pivot_table(index=["comid", "year"], columns="product", values="kg", aggfunc="sum")


def incremental_information(wide: pd.DataFrame, exclude=()) -> pd.DataFrame:
    """Per column: R2 when regressed on every OTHER column. High means already implied by the pool.

    `exclude` drops columns that are redundant BY CONSTRUCTION -- the gTREND closure totals are their own parts summed, so leaving them in would report a perfect fit and say nothing.
    """
    d = wide.drop(columns=[c for c in exclude if c in wide.columns]).dropna()
    cols = list(d.columns)
    if len(cols) < 2 or not len(d):
        return pd.DataFrame(columns=["product", "r2_given_others", "verdict"])
    x = d.to_numpy("float64")
    x = (x - x.mean(0)) / np.where(x.std(0) == 0, 1.0, x.std(0))
    rows = []
    for i, c in enumerate(cols):
        y = x[:, i]
        others = np.delete(x, i, axis=1)
        a = np.hstack([others, np.ones((len(others), 1))])
        beta, *_ = np.linalg.lstsq(a, y, rcond=None)
        resid = y - a @ beta
        ss_tot = float((y ** 2).sum())
        r2 = 1.0 - float((resid ** 2).sum()) / ss_tot if ss_tot > 0 else np.nan
        rows.append(dict(product=c, r2_given_others=round(r2, 4),
                         verdict=("reconstructible from the pool" if r2 > 0.95
                                  else "largely implied" if r2 > 0.8 else "carries its own signal")))
    return pd.DataFrame(rows).sort_values("r2_given_others", ascending=False).reset_index(drop=True)


def spearman_matrix(wide: pd.DataFrame) -> pd.DataFrame:
    """Rank correlation between products. Reported alongside, never instead of, `incremental_information`.

    Zero-variance columns are dropped first: a constant correlates with nothing and pandas returns NaN for the whole row, which reads as "no relationship" when it means "no variation".
    """
    d = wide.dropna()
    keep = [c for c in d.columns if d[c].nunique(dropna=True) > 1]
    return d[keep].corr(method="spearman").round(3)


def top_pairs(wide: pd.DataFrame, n: int = 8) -> pd.DataFrame:
    """The `n` most rank-correlated product pairs, long-form, for a report table."""
    m = spearman_matrix(wide)
    if m.empty:
        return pd.DataFrame(columns=["a", "b", "spearman"])
    mask = np.triu(np.ones(m.shape, dtype=bool), 1)
    # The axes carry the pivot's own name ("product"), so they are renamed BEFORE reset_index --
    # otherwise both levels land on one column name and pandas refuses the insert.
    stacked = m.where(mask).stack()
    stacked.index = stacked.index.set_names(["a", "b"])
    pairs = stacked.rename("spearman").reset_index().dropna(subset=["spearman"])
    return pairs.sort_values("spearman", ascending=False).head(n).reset_index(drop=True)


def variance_decomposition(wide: pd.DataFrame) -> pd.DataFrame:
    """Per product: the share of variance that is between-catchment, between-year, and interaction.

    A two-way decomposition on the (catchment x year) panel. `space` is a static map, `time` a national trend applied everywhere, and `interaction` the only part that is genuinely spatiotemporal -- the part a model cannot get from a static covariate plus a year dummy.
    """
    rows = []
    for c in wide.columns:
        s = wide[c].dropna()
        if not len(s):
            continue
        panel = s.unstack("year")
        panel = panel.dropna(axis=0, how="any")
        if panel.shape[0] < 2 or panel.shape[1] < 2:
            continue
        x = panel.to_numpy("float64")
        mu = x.mean()
        a = x.mean(1) - mu                              # catchment effects
        b = x.mean(0) - mu                              # year effects
        ss_tot = float(((x - mu) ** 2).sum())
        if ss_tot <= 0:
            continue
        ss_space = float(x.shape[1] * (a ** 2).sum())
        ss_time = float(x.shape[0] * (b ** 2).sum())
        ss_int = max(ss_tot - ss_space - ss_time, 0.0)
        rows.append(dict(product=c,
                         space=round(ss_space / ss_tot, 4),
                         time=round(ss_time / ss_tot, 4),
                         interaction=round(ss_int / ss_tot, 4),
                         verdict=("static map + year dummy" if ss_int / ss_tot < 0.05
                                  else "mostly spatial" if ss_space / ss_tot > 0.9
                                  else "spatiotemporal")))
    cols = ["product", "space", "time", "interaction", "verdict"]
    if not rows:                                        # every product constant: an answer, not a crash
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows, columns=cols).sort_values("interaction").reset_index(drop=True)


def missingness(comids=None) -> pd.DataFrame:
    """Per COMID-keyed store: how many of a catchment sample it covers, and whether the gap tracks catchment size.

    A gap that correlates with area is structured -- headwater or coastal catchments the source never modelled -- and a model should see a flag rather than an imputed zero. `absent is not zero` protects the join; it does not carry that information forward.
    """
    from data.access import api

    comids = sample_comids() if comids is None else np.asarray(comids)
    vaa = api.get_network("vaa")
    area = (pd.to_numeric(vaa.areasqkm, errors="coerce")
            .groupby(pd.to_numeric(vaa.comid, errors="coerce").astype("int64")).first())
    rows = []
    for source in ("streamcat", "streamcat_series"):
        try:
            got = api.get_attributes(comids, columns=[], source=source)
        except Exception:                                # noqa: BLE001 -- store absent is a real answer
            continue
        present = set(got.comid.astype("int64"))
        miss = np.array([c not in present for c in comids])
        a_all = area.reindex(comids)
        rows.append(dict(store=source, n=len(comids), missing=int(miss.sum()),
                         frac_missing=round(float(miss.mean()), 4),
                         median_area_present=round(float(a_all[~miss].median()), 3) if (~miss).any() else np.nan,
                         median_area_missing=round(float(a_all[miss].median()), 3) if miss.any() else np.nan))
    return pd.DataFrame(rows)


# ---- battery F: is nullity predictable from position? -------------------------------------------------

WIECZOREK_PROBE = ("wieczorek_soils_cat", "wieczorek_geology_cat", "wieczorek_hydrologic_cat",
                   "wieczorek_topographic_cat")


def _null_structure(null: np.ndarray, basin) -> dict:
    """How concentrated a null pattern is across basins, and therefore whether imputing it smuggles geography in.

    Under random nullity at rate p, every basin's null rate is p up to binomial noise, no basin is wholly null, and the worst-tenth of basins hold about a tenth of the nulls. Departures on those three are what "structured" means, and they are reported rather than a single test statistic because they fail in distinguishable ways: a wholly-null basin is a coverage hole, while a merely elevated rate is a gradient.
    """
    d = pd.DataFrame({"null": np.asarray(null, dtype=bool), "b": np.asarray(basin)}).dropna(subset=["b"])
    if not len(d) or not d["null"].any():
        return {"null_rate": round(float(d["null"].mean()) if len(d) else 0.0, 4), "n_null": 0,
                "basins_all_null": 0, "share_in_worst_decile": np.nan, "verdict": "no nulls"}
    per = d.groupby("b")["null"].agg(["mean", "sum", "size"])
    n_basin = len(per)
    n_null = int(per["sum"].sum())
    k = max(1, int(round(0.10 * n_basin)))
    worst = per.nlargest(k, "mean")
    share = float(worst["sum"].sum() / n_null)
    all_null = int((per["mean"] > 0.999).sum())
    # THE WORST-DECILE SHARE IS BIASED UPWARD WHEN NULLS ARE FEW. The decile is chosen after seeing the
    # data, so with fewer nulls than basins even a random scatter lands them all inside the top tenth by
    # rate -- 16 nulls over 120 basins reads as 100% concentrated and means nothing. Require enough nulls
    # that a random pattern would genuinely spread across basins before calling anything structured.
    enough = n_null >= 3 * n_basin
    if not enough:
        verdict = f"inconclusive — only {n_null} null(s) over {n_basin} basins"
    else:
        # 0.10 is the random expectation; 0.5 means half the nulls sit in a tenth of the basins
        verdict = ("structured — imputation smuggles geography" if share > 0.35 or all_null
                   else "diffuse" if share < 0.20 else "mildly clustered")
    return {"null_rate": round(float(d["null"].mean()), 4), "n_null": n_null,
            "basins_all_null": all_null, "share_in_worst_decile": round(share, 4), "verdict": verdict}


def missingness_by_position(n_basins: int = 120) -> pd.DataFrame:
    """Per attribute family: the null rate, and whether the nulls cluster by basin rather than scatter.

    Position is taken as the HUC8 the reach sits in, which is free from `reachcode` and is the grain at which a coverage hole would actually bite. Sampling is by WHOLE basins for the same reason the variance decomposition needs it: a scattered catchment sample cannot show clustering.
    """
    import glob

    import pyarrow.parquet as pq

    from data.build import config as bcfg
    from data.insights import geometry

    m = geometry.sample_basins(n_basins)
    b = m.set_index("comid").huc8
    comids = set(m.comid.tolist())
    rows = []

    for fam in WIECZOREK_PROBE:
        parts = sorted(glob.glob(str(bcfg.ACQ_ATTRIBUTES / fam / "part_*.parquet")))
        if not parts:
            continue
        # THE QUESTION IS PER-COLUMN NODATA, NOT WHOLE-ROW ABSENCE. No Wieczorek catchment is null across a
        # whole family, so an all-columns test reports "no nulls" and misses the thing the audit names.
        # The worst-null column is found from FOOTER STATISTICS -- no rows read -- and only that is probed.
        # EACH PART CARRIES ITS OWN COLUMNS: the pull partitions a family by attribute batch, not by comid
        # range, so a column in part_000 is absent from part_001 and a shared column list fails.
        best = (None, None, 0.0)
        for q in parts:
            pf = pq.ParquetFile(q)
            md = pf.metadata
            for i, name in enumerate(pf.schema_arrow.names):
                if name == "comid":
                    continue
                nulls = sum(md.row_group(rg).column(i).statistics.null_count
                            for rg in range(md.num_row_groups)
                            if md.row_group(rg).column(i).statistics is not None)
                if md.num_rows and nulls / md.num_rows > best[2]:
                    best = (q, name, nulls / md.num_rows)
        if best[1] is None or best[2] <= 0:
            rows.append(dict(family=fam, column="(none null)", n=0, null_rate=0.0, n_null=0,
                             basins_all_null=0, share_in_worst_decile=np.nan, verdict="no nulls"))
            continue
        d = pq.read_table(best[0], columns=["comid", best[1]],
                          filters=[("comid", "in", comids)]).to_pandas()
        if not len(d):
            continue
        rows.append(dict(family=fam, column=best[1], n=len(d),
                         **_null_structure(d[best[1]].isna().to_numpy(), d.comid.map(b))))

    try:
        from data.access import api

        got = api.get_attributes(sorted(comids), columns=[], source="streamcat")
        present = set(got.comid.astype("int64"))
        ids = np.array(sorted(comids))
        rows.append(dict(family="streamcat", column="(row absent)", n=len(ids),
                         **_null_structure(np.array([c not in present for c in ids]),
                                           pd.Series(ids).map(b))))
    except Exception:                                    # noqa: BLE001
        pass
    return pd.DataFrame(rows)
