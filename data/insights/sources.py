"""Tier 1 -- what each source IS: grain, span, cadence, lineage, and how much of the cohort it reaches.

LINEAGE IS A COLUMN HERE BECAUSE NO STATISTIC CAN RECOVER IT. StreamCat's `n_ff` and gTREND's `Fertilizer_Ag` both descend from the same USGS county fertilizer estimates; they will correlate beautifully, and that correlation says nothing about whether they are two measurements or one measurement twice. Tier 2 can measure agreement and redundancy; only reading the documentation can distinguish "agrees because both are right" from "agrees because both are the same data". So lineage is traced by hand, recorded here, and the do-not-double-count list is derived from it.
"""

from __future__ import annotations

import pandas as pd

# One row per source the pipeline holds. `native_grain` is the unit the source publishes at, BEFORE our
# reduction; `lineage` names what it descends from, and sources sharing an ancestor are not independent.
SOURCES = [
    dict(source="gridmet_daily", theme="meteorological_forcing", native_grain="1/24 deg cell (~19 km2)",
         cadence="daily", span="1979-present", lineage="gauge interpolation + PRISM sharpening",
         store="acquired/weather/daily"),
    dict(source="gridmet_drought_pentad", theme="antecedent_moisture_and_snow",
         native_grain="1/24 deg cell", cadence="pentad (5-day)", span="1979-present",
         lineage="derived from gridMET's own pr/pet", store="acquired/weather/pentad"),
    dict(source="cdl_crops", theme="crops_and_land_cover", native_grain="30 m pixel", cadence="annual",
         span="2008-2025", lineage="USDA NASS classification of Landsat/Sentinel",
         store="derived/covariates/crops.parquet"),
    dict(source="gtrend_fertilizer", theme="nitrogen_inputs", native_grain="250 m pixel", cadence="annual",
         span="2000-2017", lineage="TREND county totals downscaled by an agricultural mask",
         store="raw/gTREND/Agriculture_Fertilizer"),
    dict(source="gtrend_livestock", theme="nitrogen_inputs", native_grain="250 m pixel", cadence="annual",
         span="2000-2017", lineage="TREND county livestock inventories downscaled",
         store="raw/gTREND/Lvst_Sum"),
    dict(source="gtrend_fixation", theme="nitrogen_inputs", native_grain="250 m pixel", cadence="annual",
         span="2000-2017", lineage="TREND, crop-area based", store="raw/gTREND/*_Fixation"),
    dict(source="gtrend_uptake", theme="nitrogen_sinks_and_uptake", native_grain="250 m pixel",
         cadence="annual", span="2000-2017", lineage="TREND, yield based", store="raw/gTREND/*_Uptake"),
    dict(source="gtrend_deposition", theme="nitrogen_inputs", native_grain="250 m pixel", cadence="annual",
         span="2000-2017", lineage="NADP/CMAQ deposition surfaces", store="raw/gTREND/Atmospheric_*"),
    dict(source="streamcat_n_families", theme="nitrogen_inputs", native_grain="NHDPlus catchment",
         cadence="annual", span="1987-2017",
         lineage="TREND county totals -- SHARES AN ANCESTOR WITH gtrend_*",
         store="acquired/attributes/streamcat.parquet"),
    dict(source="wieczorek_chemical", theme="nitrogen_inputs", native_grain="NHDPlus catchment",
         cadence="static / multi-vintage", span="varies", lineage="USGS compilations",
         store="acquired/attributes/wieczorek_chemical_*"),
    dict(source="dmr_loads", theme="point_sources_and_structures", native_grain="permitted outfall",
         cadence="monthly", span="2008-2026", lineage="self-reported effluent monitoring (ICIS-NPDES)",
         store="derived/covariates/dmr_loads.parquet"),
    dict(source="agtile", theme="artificial_drainage", native_grain="30 m pixel", cadence="static (2017)",
         span="2017", lineage="Census of Ag county tile stats + NLCD + SRTM slope + SSURGO drainage class",
         store="derived/covariates/agtile.parquet"),
    dict(source="wieczorek_hydrologic_modifications", theme="artificial_drainage",
         native_grain="NHDPlus catchment", cadence="static (1992)", span="1992",
         lineage="USGS compilation -- SHARES SSURGO WITH agtile",
         store="acquired/attributes/wieczorek_hydrologic_modifications_*"),
    dict(source="nhdplus_vaa", theme="surface_hydrology_and_flow", native_grain="reach",
         cadence="static", span="—", lineage="NHDPlus V2 value-added attributes",
         store="acquired/network"),
    dict(source="discharge_channel", theme="surface_hydrology_and_flow", native_grain="sensor",
         cadence="sub-daily to daily", span="2008-present", lineage="measured (USGS/IWQIS/MPCA)",
         store="published/water"),
    # MODELLED, NOT MEASURED, AND DELIBERATELY OUTSIDE THE STORES. Regionalised XGBoost on gridMET +
    # StreamCat + VAA, covering the 110 nitrate sites that have no gauge (48 more were refused on a
    # runoff-ratio validity check). It stays in projects/ for the same reason NWM would have: a
    # simulation must not sit in published/ wearing the shape of an observation.
    dict(source="modelled_discharge", theme="surface_hydrology_and_flow", native_grain="sensor",
         cadence="daily", span="2008-present", lineage="modelled (projects/discharge-test)",
         store="projects/discharge-test/results"),
    dict(source="wieczorek_hydrologic", theme="groundwater_and_residence_time",
         native_grain="NHDPlus catchment", cadence="static", span="—",
         lineage="USGS flow-pathway compilation", store="acquired/attributes/wieczorek_hydrologic_*"),
    dict(source="wieczorek_soils", theme="soils_and_geology", native_grain="NHDPlus catchment",
         cadence="static", span="—", lineage="STATSGO/SSURGO", store="acquired/attributes/wieczorek_soils_*"),
    dict(source="wieczorek_geology", theme="soils_and_geology", native_grain="NHDPlus catchment",
         cadence="static", span="—", lineage="Olson lithology and others",
         store="acquired/attributes/wieczorek_geology_*"),
    dict(source="wieczorek_regions", theme="physiography_and_regions", native_grain="NHDPlus catchment",
         cadence="static", span="—", lineage="HLR / Fenneman / EPA ecoregions",
         store="acquired/attributes/wieczorek_regions_*"),
    dict(source="dams", theme="point_sources_and_structures", native_grain="dam point", cadence="static",
         span="—", lineage="National Inventory of Dams", store="derived/covariates/dams.parquet"),
    dict(source="facilities", theme="point_sources_and_structures", native_grain="outfall point",
         cadence="static", span="—", lineage="ICIS-NPDES + CWNS",
         store="derived/covariates/facilities.parquet"),
    dict(source="usgs", theme="water_quality_observations", native_grain="sensor",
         cadence="sub-daily to daily", span="2008-present", lineage="measured", store="published/water"),
    dict(source="iwqis", theme="water_quality_observations", native_grain="sensor", cadence="sub-daily",
         span="2008-present", lineage="measured", store="published/water"),
    dict(source="mpca", theme="water_quality_observations", native_grain="sensor", cadence="sub-daily",
         span="2008-present", lineage="measured", store="published/water"),
]

# Pairs that are NOT independent, established by reading lineage rather than by correlating values.
# A recipe feeding both members of a pair is counting one measurement twice.
DO_NOT_DOUBLE_COUNT = [
    ("streamcat_n_families", "gtrend_fertilizer",
     "both descend from the same TREND county fertilizer estimates; StreamCat aggregates them to "
     "catchments, gTREND downscales them by an agricultural mask"),
    ("streamcat_n_families", "gtrend_livestock", "same TREND ancestry, same relationship"),
    ("agtile", "wieczorek_hydrologic_modifications",
     "AgTile is derived FROM SSURGO drainage class and slope, which the Wieczorek soils and topographic "
     "themes also carry directly"),
    ("gtrend_fixation:fixation_ag", "gtrend_fixation:fixation_cropland + fixation_pasture",
     "an exact arithmetic sum, verified to 0.01 -- the total is the parts"),
    ("gtrend_uptake:uptake_ag", "gtrend_uptake:uptake_cropland + uptake_pasture",
     "an exact arithmetic sum, verified to 0.01"),
    ("gtrend_livestock:livestock", "gtrend_livestock:livestock_hogs",
     "hogs are a SUBSET of the livestock total, not a partner"),
]


def table() -> pd.DataFrame:
    return pd.DataFrame(SOURCES)


def double_count_table() -> pd.DataFrame:
    return pd.DataFrame(DO_NOT_DOUBLE_COUNT, columns=["a", "b", "why"])


# ---- battery A: coverage ------------------------------------------------------------------------------

def coverage_table(comid_sample: int = 20_000) -> "pd.DataFrame":
    """Per COMID-keyed source: what share of a catchment sample it reaches, and over what span.

    "Does this source even reach our cohort" is the question to answer before anything subtler, because a redundancy or agreement statistic computed on the rows a source happens to cover says nothing about the rows it does not. Span is reported against the water record's own 2008-2026 window, so a source ending in 2017 reads as the slowly-varying descriptor it is rather than as a contemporaneous covariate.
    """
    import numpy as np
    import pandas as pd
    import pyarrow.parquet as pq

    from data.access import api, config as acfg
    from data.insights import agreement

    comids = agreement.sample_comids(comid_sample)
    n = len(comids)
    rows = []

    for name, path, col in (("crops (CDL)", acfg.PUB_COMID_FEATURES / "crops.parquet", "frac"),
                            ("nutrients (gTREND)", acfg.PUB_COMID_FEATURES / "nutrients.parquet", "kg_per_ha"),
                            ("agtile", acfg.PUB_COMID_FEATURES / "agtile.parquet", "tile_px")):
        if not path.exists():
            continue
        cols = ["comid", col] + (["year"] if "year" in pq.ParquetFile(path).schema_arrow.names else [])
        t = pq.read_table(path, columns=cols, filters=[("comid", "in", set(int(c) for c in comids))]).to_pandas()
        have = t.loc[t[col].notna(), "comid"].nunique()
        yrs = (f"{int(t.year.min())}-{int(t.year.max())}" if "year" in t.columns and len(t) else "static")
        rows.append(dict(source=name, reached=have, of=n, frac=round(have / n, 4), span=yrs))

    for src in ("streamcat", "streamcat_series"):
        try:
            a = api.get_attributes(comids, columns=[], source=src)
        except Exception:                                # noqa: BLE001
            continue
        rows.append(dict(source=f"StreamCat ({src.split('_')[-1] if '_' in src else 'latest'})",
                         reached=a.comid.nunique(), of=n,
                         frac=round(a.comid.nunique() / n, 4),
                         span="1987-2016" if src.endswith("series") else "latest vintage"))

    out = pd.DataFrame(rows)
    if len(out):
        out["verdict"] = np.where(out.frac > 0.97, "reaches the cohort",
                                  np.where(out.frac > 0.80, "partial", "thin"))
    return out


# ---- per-theme redundancy: the column registry the themes deliberately lack --------------------------

# A THEME NAMES SOURCES, NOT COLUMNS, and that is right -- `themes.toml` enumerates the domain so a gap can
# be visible, and threading column plumbing through it would tie a conceptual list to a storage layout.
# The correspondence has to live somewhere though, so it lives here: one representative, comparable column
# per source per theme. Representative is a JUDGEMENT, which is why this is a hand-written table and not
# derived -- picking `pctagdrainagecat` over `pctagdrainagews` is a claim about which one the theme means.
#
# A theme absent from this table gets no matrix, and the report says so rather than omitting it silently.
THEME_PROBES = {
    "nitrogen_inputs": [
        ("gTREND fertilizer", "nutrients", "fertilizer"),
        ("gTREND livestock", "nutrients", "livestock"),
        ("gTREND hogs", "nutrients", "livestock_hogs"),
        ("gTREND dep. oxidized", "nutrients", "deposition_oxidized"),
        ("gTREND dep. reduced", "nutrients", "deposition_reduced"),
        ("gTREND surplus", "nutrients", "surplus"),
        ("StreamCat n_ff (ws)", "streamcat_series", "n_ff_2016ws"),
        ("StreamCat n_lw (ws)", "streamcat_series", "n_lw_2016ws"),
        ("StreamCat n_dep (ws)", "streamcat_series", "n_dep_2016ws"),
    ],
    "nitrogen_sinks_and_uptake": [
        ("gTREND uptake cropland", "nutrients", "uptake_cropland"),
        ("gTREND uptake pasture", "nutrients", "uptake_pasture"),
        ("gTREND fix. cropland", "nutrients", "fixation_cropland"),
        ("gTREND fix. pasture", "nutrients", "fixation_pasture"),
        ("StreamCat n_cr (ws)", "streamcat_series", "n_cr_2016ws"),
        ("CDL corn", "crops", 1),
        ("CDL soy", "crops", 5),
    ],
    "artificial_drainage": [
        ("AgTile fraction", "agtile", None),
        ("StreamCat drainage (cat)", "streamcat", "pctagdrainagecat"),
        ("StreamCat drainage (ws)", "streamcat", "pctagdrainagews"),
    ],
    "soils_and_geology": [
        ("Wieczorek HG-A (well drained)", "wieczorek", ("wieczorek_soils_cat", "CAT_HGA")),
        ("Wieczorek HG-D (poorly drained)", "wieczorek", ("wieczorek_soils_cat", "CAT_HGD")),
        ("Wieczorek Olson K", "wieczorek", ("wieczorek_geology_cat", "CAT_OLSON_K")),
        ("Wieczorek Olson CaO", "wieczorek", ("wieczorek_geology_cat", "CAT_OLSON_CAO")),
        ("StreamCat clay", "streamcat", "claycat"),
        ("StreamCat sand", "streamcat", "sandcat"),
    ],
    "physiography_and_regions": [
        ("Wieczorek basin slope", "wieczorek", ("wieczorek_topographic_cat", "CAT_BASIN_SLOPE")),
        ("Wieczorek mean elevation", "wieczorek", ("wieczorek_topographic_cat", "CAT_ELEV_MEAN")),
        ("Wieczorek stream slope", "wieczorek", ("wieczorek_topographic_cat", "CAT_STREAM_SLOPE")),
        ("Wieczorek stream length", "wieczorek", ("wieczorek_topographic_cat", "CAT_STREAM_LENGTH")),
    ],
    "crops_and_land_cover": [
        ("CDL corn", "crops", 1),
        ("CDL soy", "crops", 5),
        ("CDL grass/pasture", "crops", 176),
        ("CDL deciduous forest", "crops", 141),
        ("CDL developed/open", "crops", 121),
    ],
}

_PROBE_YEAR = 2017


def theme_frame(theme: str, comids) -> "pd.DataFrame":
    """comid x labelled column for one theme's probes, assembled across stores."""
    import pandas as pd
    import pyarrow.parquet as pq

    from data.access import api, config as acfg

    probes = THEME_PROBES.get(theme, [])
    ids = set(int(c) for c in comids)
    cols = {}
    for label, kind, spec in probes:
        try:
            if kind == "nutrients":
                t = pq.read_table(acfg.PUB_COMID_FEATURES / "nutrients.parquet",
                                  columns=["comid", "kg_per_ha"],
                                  filters=[("comid", "in", ids), ("product", "=", spec),
                                           ("year", "=", _PROBE_YEAR)]).to_pandas()
                cols[label] = t.set_index("comid").kg_per_ha
            elif kind == "crops":
                t = pq.read_table(acfg.PUB_COMID_FEATURES / "crops.parquet",
                                  columns=["comid", "frac"],
                                  filters=[("comid", "in", ids), ("code", "=", spec),
                                           ("year", "=", 2020)]).to_pandas()
                cols[label] = t.groupby("comid").frac.sum()
            elif kind == "agtile":
                t = pq.read_table(acfg.PUB_COMID_FEATURES / "agtile.parquet",
                                  columns=["comid", "tile_px", "n_px"],
                                  filters=[("comid", "in", ids)]).to_pandas()
                cols[label] = (t.tile_px / t.n_px.replace(0, float("nan"))).groupby(t.comid).first()
            elif kind == "wieczorek":
                import glob

                from data.build import config as bcfg

                fam, col = spec
                got = None
                for q in sorted(glob.glob(str(bcfg.ACQ_ATTRIBUTES / fam / "part_*.parquet"))):
                    if col not in pq.ParquetFile(q).schema_arrow.names:
                        continue          # each part carries its OWN columns, see missingness_by_position
                    d = pq.read_table(q, columns=["comid", col],
                                      filters=[("comid", "in", ids)]).to_pandas()
                    got = d.set_index("comid")[col]
                    break
                if got is not None:
                    cols[label] = got
            elif kind in ("streamcat", "streamcat_series"):
                a = api.get_attributes(sorted(ids), columns=[spec], source=kind)
                if spec in a.columns:
                    cols[label] = a.set_index("comid")[spec]
        except Exception:                                # noqa: BLE001 -- a missing probe is a blank column
            continue
    return pd.DataFrame(cols) if cols else pd.DataFrame()


def theme_matrix(theme: str, comids) -> "pd.DataFrame":
    """Spearman matrix over a theme's probes. Zero-variance columns dropped: a constant correlates with nothing and NaN would read as 'no relationship'."""
    d = theme_frame(theme, comids).dropna()
    keep = [c for c in d.columns if d[c].nunique(dropna=True) > 1]
    return d[keep].corr(method="spearman").round(3) if len(keep) >= 2 else pd.DataFrame()
