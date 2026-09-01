"""The access surface: published primitives one join away, read-only, stamps checked, no build imports.

Every reader takes the published store as it is; durable changes enter through the build's decision ledgers and arrive at the next build. Caches (the pooled read) are optimizations with live fallbacks, never prerequisites. The flow-network layers are read from the ACQUIRED archive in place -- it is immutable and snapshot-manifested, and copying gigabytes into `published/` would duplicate an archive to no reader's benefit.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config
from .machinery import accumulate as acc_mod
from .machinery import aggregate as agg_mod
from .machinery import snap as snap_mod


def get_sensors(columns=None) -> pd.DataFrame:
    """The published sensors metadata table: every registration's full story, one row each."""
    return pd.read_parquet(config.PUB_SENSORS, columns=columns)


def get_record(code_or_uid: str, channels=None, window=None) -> pd.DataFrame:
    """One published record's daily series with per-day provenance, by SNR code (source uids resolve through the metadata as a convenience)."""
    code = str(code_or_uid)
    if not code.startswith("SNR-"):
        s = get_sensors(columns=["site_uid", "publishes_under"]).set_index("site_uid")
        if code not in s.index or pd.isna(s.loc[code, "publishes_under"]):
            raise KeyError(f"{code_or_uid!r} resolves to no published record")
        code = str(s.loc[code, "publishes_under"])
    p = config.PUB_WATER / f"{code}.parquet"
    if not p.exists():
        raise KeyError(f"no published record for {code}")
    d = pd.read_parquet(p)
    if channels is not None:
        keep = ["date"] + [c for c in d.columns
                           if any(c.startswith(f"{ch}_") for ch in channels)]
        d = d[keep]
    if window is not None:
        lo, hi = window
        dt = pd.to_datetime(d.date)
        d = d[(dt >= pd.Timestamp(lo)) & (dt <= pd.Timestamp(hi))]
    return d.reset_index(drop=True)


def get_native(uid: str, channels=None, window=None) -> pd.DataFrame:
    """The sensor's NATIVE sub-daily observations, UTC-indexed, exactly as the source published them.

    THIS IS THE ONLY WAY TO CHOOSE YOUR OWN DAY. The published record is collapsed to Central Standard calendar days (`io.STANDARD_TZ`, "the one place a day is decided"), which is a choice the build had to make and cannot un-make for you. gridMET's precipitation day runs about 14:00-14:00 UTC, eight hours off ours, and no relabelling of a daily series can repair that -- but a model holding the native samples can bin them onto whatever boundary its covariates use, gridMET's included.

    Parameters
    ----------
    uid : str
        Source-scoped sensor id (`USGS:05412500`), NOT an SNR code -- native series are per SENSOR, before
        identity merged them into records.
    channels : Sequence[str], optional
        Native column names to return; default every column on file.
    window : tuple, optional
        (start, end) timestamps, inclusive. Naive values are read as UTC.

    Returns
    -------
    pd.DataFrame
        Indexed by UTC `datetime`. Columns are the SOURCE's native names and units (`00060_dv`,
        `99133_uv`, ...), NOT the registry's canonical channels -- no scrub, no unit conversion, no
        sentinel masking has been applied. `data.build.registry` holds the mapping and the fill values.

    See Also
    --------
    get_record : the published daily record, scrubbed and canonicalised, keyed by SNR code.

    Notes
    -----
    About 59% of sensors carry genuinely sub-daily samples (5- or 15-minute); the rest publish USGS daily
    values, where the native step is already a day and nothing finer exists to re-bin.
    """
    p = config.ACQ_WATER_NATIVE / f"{uid.replace(':', '__')}.parquet"
    if not p.exists():
        raise FileNotFoundError(f"no native series for {uid!r} ({p})")
    d = pd.read_parquet(p, columns=list(channels) if channels else None)
    if window is not None:
        lo, hi = (pd.Timestamp(w) for w in window)
        idx = pd.DatetimeIndex(d.index)
        lo = lo.tz_localize("UTC") if lo.tzinfo is None else lo
        hi = hi.tz_localize("UTC") if hi.tzinfo is None else hi
        d = d[(idx >= lo) & (idx <= hi)]
    return d


def get_quality() -> pd.DataFrame:
    """Every (record, channel) quality verdict with its masks -- why any masked day is absent."""
    return pd.read_parquet(config.PUB_QUALITY)


def get_proximity(max_sep_m: float | None = None) -> pd.DataFrame:
    """The published pair table: every located pair within 2 km of STATED coordinates, with rectangle bounds."""
    d = pd.read_parquet(config.PUB_PROXIMITY)
    return d[d.sep_m <= max_sep_m].reset_index(drop=True) if max_sep_m is not None else d


def get_network(layer: str = "flowlines"):
    """A flow-network layer as the authority provides it: flowlines | vaa | catchments | waterbodies."""
    p = config.ACQ_NETWORK / f"{layer}.parquet"
    if not p.exists():
        raise FileNotFoundError(f"network layer {layer!r} is not on disk ({p})")
    if layer == "vaa":
        return pd.read_parquet(p)
    import geopandas as gpd

    return gpd.read_parquet(p)


def get_covariates(comids, products=("crops", "nutrients", "agtile"), years=None, remap=None) -> dict:
    """Catchment-grain covariates aggregated over a plain COMID set (weight 1). For polygons or custom weights, build the set with `machinery.aggregate` and call its functions directly."""
    wset = agg_mod.from_comids(comids)
    out = {}
    for prod in products:
        if prod == "crops":
            out["crops"] = agg_mod.crops(wset, years=years, remap=remap)
        elif prod == "nutrients":
            out["nutrients"] = agg_mod.nutrients(wset, years=years)
        elif prod == "agtile":
            out["agtile"] = agg_mod.agtile(wset)
        else:
            out[prod] = pd.read_parquet(config.PUB_COMID_FEATURES / f"{prod}.parquet") \
                .query("comid in @comids")
    out["coverage"] = agg_mod.coverage_gap(wset)
    return out


# The COMID-keyed attribute stores, by the name a caller asks for. `wieczorek` resolves to its themed
# directories, which are partitioned rather than single files.
ATTRIBUTE_SOURCES = {
    "streamcat": "streamcat.parquet",
    "streamcat_series": "streamcat_series.parquet",
    "nid": "nid.parquet",
    "sekellick": "sekellick_n.parquet",
}


def attribute_columns(source: str = "streamcat") -> list[str]:
    """Every column name in an attribute store, read from the footer alone -- no rows.

    The pairing to know: StreamCat names end `cat` (the local catchment) or `ws` (the accumulated watershed), so `n_ff_1990cat` and `n_ff_1990ws` are the same metric at two grains. `streamcat_series` carries 326 of each across eleven nitrogen families; `streamcat` carries the latest vintage of everything else.
    """
    import pyarrow.parquet as pq

    if source not in ATTRIBUTE_SOURCES:
        raise ValueError(f"unknown attribute source {source!r}; known: {sorted(ATTRIBUTE_SOURCES)}")
    p = config.ACQ_ATTRIBUTES / ATTRIBUTE_SOURCES[source]
    if not p.exists():
        raise FileNotFoundError(f"{source} is not on disk ({p})")
    return list(pq.ParquetFile(p).schema_arrow.names)


def get_attributes(comids, columns=None, source: str = "streamcat") -> pd.DataFrame:
    """COMID-keyed catchment attributes, filtered to `comids` before any row is materialised.

    Parameters
    ----------
    comids : Sequence[int]
        The reaches wanted. REQUIRED, and the reason is size: `streamcat_series` is 7.3 GB over 1.48M COMIDs and 653 metrics, so an unfiltered read is a memory event rather than a slow query.
    columns : Sequence[str], optional
        Metric names; default every column. `comid` is always included. See `attribute_columns`.
    source : {'streamcat', 'streamcat_series', 'nid', 'sekellick'}, default 'streamcat'
        `streamcat` is the 296-column latest-vintage pull; `streamcat_series` the eleven nitrogen families as annual series; `nid` the dam inventory; `sekellick` the confined/unconfined manure and fertiliser split at four census years (2002, 2007, 2012, 2017) — the only source on disk separating CAFO manure from pasture.

    Returns
    -------
    pd.DataFrame
        One row per matched COMID. A COMID absent from the store is absent here -- StreamCat covers 1,478,330 of 1,478,684 askable reaches, so a missing row means the service had nothing, not that the join failed.

    See Also
    --------
    attribute_columns : the names available in a store, from the footer.
    get_covariates : the derived per-catchment products, which are computed here rather than fetched.
    """
    import pyarrow.parquet as pq

    if source not in ATTRIBUTE_SOURCES:
        raise ValueError(f"unknown attribute source {source!r}; known: {sorted(ATTRIBUTE_SOURCES)}")
    p = config.ACQ_ATTRIBUTES / ATTRIBUTE_SOURCES[source]
    if not p.exists():
        raise FileNotFoundError(f"{source} is not on disk ({p})")
    want = list(comids)
    cols = None
    if columns is not None:
        cols = ["comid"] + [c for c in columns if c != "comid"]
    # PUSHED DOWN, not filtered after the fact: `comid` is not sorted across row groups, so statistics
    # prune little, but the filter still keeps the materialised table to the matched rows rather than
    # building 1.48M x 653 and subsetting it.
    t = pq.read_table(p, columns=cols, filters=[("comid", "in", set(want))])
    return t.to_pandas()


def get_weather(wset_or_comids, window, variables=("pr", "tmmx", "tmmn", "pet", "srad")) -> pd.DataFrame:
    """Area-weighted daily weather over a catchment set. One row per day, one column per variable.

    THE `date` COLUMN IS gridMET'S DAY, NOT YOURS, AND THEY ARE EIGHT HOURS APART. gridMET accumulates
    precipitation over roughly 14:00-14:00 UTC (measured: a 24-offset correlation scan against AORC hourly
    peaks sharply at 13-15 h, r = 0.969, against 0.474 at a UTC calendar day); the published records bin
    observations to Central Standard calendar days, 06:00-06:00 UTC. gridMET day D therefore overlaps your
    day D by sixteen hours and your day D+1 by eight.

    THIS FUNCTION DOES NOT SHIFT THE LABEL, and that is deliberate rather than an omission. Sixteen of
    twenty-four is already the best integer-day assignment available: measured against AORC aggregated on
    the Central Standard boundary, the label as published scores r = 0.798, a one-day shift 0.385, and a
    shift the other way 0.137. Relabelling moves all of the water, not the misaligned third.

    What the offset costs is real -- 69% mean absolute daily disagreement in rainfall at Ames in 2020 -- and
    it cannot be repaired here. A model that cares should either lag its precipitation features
    deliberately, knowing the eight hours, or use precipitation re-derived from AORC hourly on its own
    boundary. `acquire.weather.GRIDMET_DAY_OFFSET_H` carries the measurement.
    """
    """Daily weather over any basin: collapse to (cell_id, w_km2) FIRST, then read only the touched cells' days and take the weighted mean. One row per day."""
    wset = (wset_or_comids if isinstance(wset_or_comids, pd.DataFrame)
            else agg_mod.from_comids(wset_or_comids))
    cells = agg_mod.weather_weights(wset)
    if not len(cells):
        return pd.DataFrame(columns=["date", *variables])
    lo, hi = pd.Timestamp(window[0]), pd.Timestamp(window[1])
    frames = []
    for year in range(lo.year, hi.year + 1):
        for q in (1, 2, 3, 4):
            p = config.ACQ_WEATHER / "daily" / f"gridmet_{year}Q{q}.parquet"
            if not p.exists():
                continue
            d = pd.read_parquet(p, columns=["cell_id", "date", *variables],
                                filters=[("cell_id", "in", cells.cell_id.tolist())])
            if len(d):
                frames.append(d)
    if not frames:
        return pd.DataFrame(columns=["date", *variables])
    d = pd.concat(frames, ignore_index=True)
    d["date"] = pd.to_datetime(d.date)
    d = d[(d.date >= lo) & (d.date <= hi)]
    d = d.merge(cells, on="cell_id")
    w = d.pop("w_km2").to_numpy()
    out = {}
    for v in variables:
        # The denominator counts only cells that HAVE a value for this variable. Summing weights over
        # every row while the numerator skips NaN would drag a catchment's mean toward zero in proportion
        # to how much of it lacks data. Latent today -- the AOE clip drops all-NaN cells before storage, so
        # a full quarter measures zero nulls -- but wrong the moment one arrives.
        vals = pd.to_numeric(d[v], errors="coerce")
        ok = vals.notna()
        num = (vals.fillna(0.0) * w).groupby(d.date).sum()
        den = pd.Series(np.where(ok, w, 0.0), index=d.index).groupby(d.date).sum()
        out[v] = num / den.replace(0.0, np.nan)
    return pd.DataFrame(out).reset_index().rename(columns={"index": "date"})


DROUGHT_DEFAULT = ("spei90d", "spi90d", "eddi90d", "pdsi")


def drought_columns() -> list[str]:
    """Every aggregate in the pentad store, from a footer. See `data.build.acquire.drought` for what they are."""
    import pyarrow.parquet as pq

    ps = sorted(config.ACQ_WEATHER_PENTAD.glob("gridmet_pentad_*.parquet"))
    if not ps:
        raise FileNotFoundError(f"no pentad store ({config.ACQ_WEATHER_PENTAD})")
    return [c for c in pq.ParquetFile(ps[0]).schema_arrow.names if c not in ("cell_id", "date", "zkey")]


def get_drought(wset_or_comids, window, variables=DROUGHT_DEFAULT) -> pd.DataFrame:
    """Area-weighted drought indices over a catchment set. **One row per PENTAD, not per day.**

    Parameters
    ----------
    wset_or_comids : DataFrame or Sequence[int]
        A weighted set, or a plain COMID set given weight 1.
    window : tuple[str, str]
        (start, end). Pentads whose start falls inside the window are returned.
    variables : Sequence[str], default `DROUGHT_DEFAULT`
        Aggregate names; see `drought_columns`. `spi` has seven accumulation windows, `spei` and `eddi` eight -- there is no `spi270d`.

    Returns
    -------
    pd.DataFrame
        `pentad_start` plus one column per variable, and `.attrs['cadence'] = 'pentad'`.

        **The date column is named `pentad_start`, not `date`, deliberately.** These values cover the five days beginning at that stamp, and a frame that called it `date` would join silently against a daily series and align four days of every five to the wrong value. A consumer who wants daily rows must resample explicitly and own that choice -- forward-filling is a modelling preference, which is why the build does not store one.

    See Also
    --------
    get_weather : the daily store, same lattice and same weight matrix.
    """
    import numpy as np
    import pyarrow.parquet as pq

    wset = (wset_or_comids if isinstance(wset_or_comids, pd.DataFrame)
            else agg_mod.from_comids(wset_or_comids))
    cells = agg_mod.weather_weights(wset)
    if not len(cells):
        return pd.DataFrame(columns=["pentad_start", *variables])
    want = set(cells.cell_id.astype("int64"))
    lo, hi = pd.Timestamp(window[0]), pd.Timestamp(window[1])
    frames = []
    for p in sorted(config.ACQ_WEATHER_PENTAD.glob("gridmet_pentad_*.parquet")):
        year = int(p.stem.rsplit("_", 1)[1])
        if year < lo.year or year > hi.year:
            continue
        t = pq.read_table(p, columns=["cell_id", "date", *variables],
                          filters=[("cell_id", "in", want)]).to_pandas()
        if len(t):
            frames.append(t)
    if not frames:
        return pd.DataFrame(columns=["pentad_start", *variables])
    d = pd.concat(frames, ignore_index=True)
    d["date"] = pd.to_datetime(d.date)
    d = d[(d.date >= lo) & (d.date <= hi)].merge(cells, on="cell_id")
    w = d.pop("w_km2").to_numpy()
    out = {}
    for v in variables:
        vals = pd.to_numeric(d[v], errors="coerce")
        ok = vals.notna()
        num = (vals.fillna(0.0) * w).groupby(d.date).sum()
        den = pd.Series(np.where(ok, w, 0.0), index=d.index).groupby(d.date).sum()
        out[v] = num / den.replace(0.0, np.nan)
    res = pd.DataFrame(out).reset_index().rename(columns={"index": "pentad_start", "date": "pentad_start"})
    res.attrs["cadence"] = "pentad"
    return res


# Activities that are not an observation of the water body. A field blank reads as a near-zero nitrate
# and would not look anomalous beside a real low reading, so it is excluded by default rather than left
# for a consumer to notice.
QC_ACTIVITY_PREFIX = "Quality Control"


def get_discrete_nitrate(states=None, bbox=None, window=None, characteristics=None,
                         include_qc: bool = False, activity_types=None) -> pd.DataFrame:
    """WQX discrete nitrate samples. **These are not sensors and have no `site_uid`.**

    WQX stations are a distinct class from the instrumented sensors: discrete samples rather than a monitored series. They are deliberately absent from the census, the proximity table and the identity machinery, so they are addressed here by location and time rather than by a node identifier, and nothing joins them to `get_sensors` by key.

    Parameters
    ----------
    states : Sequence[str], optional
        Two-letter codes; default every collected state.
    bbox : tuple, optional
        `(west, south, east, north)` in degrees.
    window : tuple[str, str], optional
        `(start, end)` on the activity date.
    characteristics : Sequence[str], optional
        Default all three nitrate forms held.
    include_qc : bool, default False
        Keep quality-control activities (field blanks and the like). **Off by default**: a blank reads as a near-zero nitrate and would not look anomalous beside a real low reading.
    activity_types : Sequence[str], optional
        Filter on `Activity_TypeCode`. Worth using: a routine grab is an instant, a composite is an average over time, and a cross-sectional profile an average across the channel — about 17% of results are not plain grabs.

    Returns
    -------
    pd.DataFrame
        One row per result, with `.attrs['class'] = 'discrete_sample'`.

    See Also
    --------
    get_record : the published sensor series, which these are NOT part of.
    """
    import pyarrow.parquet as pq

    d = config.ACQ_WQX
    if not d.exists():
        return pd.DataFrame()
    ps = sorted(d.glob("nitrate_*.parquet"))
    if states:
        want = {s.upper() for s in states}
        ps = [p for p in ps if p.stem.rsplit("_", 1)[1].upper() in want]
    if not ps:
        return pd.DataFrame()
    out = pd.concat([pq.read_table(p).to_pandas() for p in ps], ignore_index=True)
    if "Activity_StartDate" in out.columns:
        out["date"] = pd.to_datetime(out.Activity_StartDate, errors="coerce")
    if window is not None and "date" in out.columns:
        lo, hi = pd.Timestamp(window[0]), pd.Timestamp(window[1])
        out = out[(out.date >= lo) & (out.date <= hi)]
    if bbox is not None:
        w, s_, e, n = bbox
        lat = pd.to_numeric(out.lat, errors="coerce")
        lon = pd.to_numeric(out.lon, errors="coerce")
        out = out[(lon >= w) & (lon <= e) & (lat >= s_) & (lat <= n)]
    if characteristics is not None and "Result_Characteristic" in out.columns:
        out = out[out.Result_Characteristic.isin(set(characteristics))]
    if activity_types is not None and "Activity_TypeCode" in out.columns:
        out = out[out.Activity_TypeCode.isin(set(activity_types))]
    if not include_qc and "Activity_TypeCode" in out.columns:
        out = out[~out.Activity_TypeCode.fillna("").str.startswith(QC_ACTIVITY_PREFIX)]
    out = out.reset_index(drop=True)
    out.attrs["class"] = "discrete_sample"
    return out


def discrete_nitrate_states() -> list[str]:
    """Which states have been collected, from the filenames alone."""
    d = config.ACQ_WQX
    return sorted(p.stem.rsplit("_", 1)[1] for p in d.glob("nitrate_*.parquet")) if d.exists() else []


def snap(lat: float, lon: float) -> dict:
    """One point onto the network: the same machinery, constants and outputs as the build's sensor snap."""
    import geopandas as gpd

    fl = get_network("flowlines")
    cat = get_network("catchments")
    vaa = get_network("vaa")
    frame = pd.DataFrame([dict(site_uid="pin", lat=lat, lon=lon,
                               source_drainage_area_km2=pd.NA)])
    out = snap_mod.snap(frame, fl, cat, vaa)
    return out.iloc[0].to_dict()


def accumulate(comid: int):
    """The upstream COMID set and its areas, by walking the published topology. See `machinery.accumulate`."""
    net = acc_mod.Network.load()
    return net.accumulate(int(comid))
