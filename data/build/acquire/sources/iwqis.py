"""IWQIS -- the Iowa Water Quality Information System sensor network.

Everything is already on disk. The registry is `raw/water/sites.csv`; the observations are the 87-file, 43,430,699-row CSV export under `raw/water/chunks/`. This adapter reads both and re-downloads nothing.

BOTH ARE PREPARED, NEITHER IS ALTERED. IIHR ships one 3.4 GB CSV and a registry whose key column and null spelling differ from what the build reads; `raw/water/split_csv.py` and `raw/water/prepare_sites.py` reconcile exactly that, once, and record what they did. The vendor drop stays beside them. A new export is therefore a re-run of two scripts and a path, not a code change.

THE EXPORT IS THE AUTHORITY, NOT A PER-SITE SPLIT. A hand-made split of it used to sit in `raw/water/IWQIS/` and be read directly, which put a DERIVED artifact in `raw/` and made it unverifiable -- and it was wrong: nine stations present in the export had no parquet at all, `WQS0031` alone carrying 832,464 rows and 824,843 nitrate observations that nothing could reach. Splitting from the export in-pipeline makes that class of loss impossible, because the per-site view is rebuilt rather than curated and its row counts are checkable against the source.

The split is materialised ONCE PER BUILD into `acquired/water/iwqis/`, not per site: all 87 files read together take about five seconds with `pyarrow.csv`, while 152 independent scans of 3.2 GB would not be affordable. It is rebuilt when any chunk is newer than the split.

WHAT THE OLD BUILD THREW AWAY. The per-site parquets carry 24 columns and the old read path selected two of them. `r_exc` and `m_exc` -- reference and measurement extinction from the optical nitrate sensor, i.e. a direct instrument-validity signal -- are present at 110 sites over 126,071 nitrate-overlapping days and were read by nothing. Turbidity is there in five variants, plus chlorophyll, phosphate, pH, spec cond and DO. All of it is kept here.

TIMESTAMPS ARE REAL CENTRAL TIME. The source CSV carries explicit -06/-05 offsets, so the stored parquets are a genuine DST-aware Central-to-UTC conversion rather than a naive relabel. Preserve that; the conversion back to a local calendar day happens once, in `io.daily_max_local`.

The export contains junk ids -- UNKNOWN, ARS/SCN/WW entries, bare numbers -- so candidates are the uids matching `WQS\\d+` or `WQP\\d+`. That is a namespace rule, not a quality filter: no site is dropped for being short, sparse or odd. `WQT` is EXCLUDED BY DECISION, not by oversight: the 2026-08 registry introduced it with one member, `WQT0001`, a 'Paired Sensor Demo Site' whose 112,603 exported rows carry no values at all -- a demo of the WQP0014/WQP0015 paired installation at Walcott rather than a station. Admitting a namespace is a deliberate act; this one was declined. `WQP` is IIHR's conservation-practice programme -- saturated buffers, blind inlets, in-field plots and a handful of river stations -- and it INVERTS THE WQS NAMING CONVENTION: `river` holds the watercourse and `nickname` holds what the station is, so `station_name` keeps both or the only evidence of a type is lost.

THE EXPORT AND THE REGISTRY DISAGREE, AND EACH IS AUTHORITATIVE FOR A DIFFERENT THING -- the export for observations, the registry for location. `materialise` filters the export and `discover` filters the registry, so a station in one and not the other falls between them. `WQP0016` was the worked case: 13,644 nitrate observations and no registry row, hence no coordinates, hence no registration `schema.validate` would accept. It was reported rather than dropped, acknowledged in the ledger, raised with IIHR, and repaired in the 2026-08 export -- which is the whole loop this function exists for. `WQS0127` now holds the same position.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from ... import config, io as bio
from ... import ledger
from . import base

NAME = "IWQIS"
DECLARES_SPANS = False      # the registry carries no per-series begin/end dates

_REGISTRY = config.RAW / "water" / "sites.csv"      # prepared by raw/water/prepare_sites.py
_CHUNKS = config.RAW / "water" / "chunks"              # the export; the authority for observations
_SPLIT = config.ACQ_WATER_IWQIS                        # per-station view, rebuilt from it
# BOTH IIHR NAMESPACES. Applied to the export in `materialise` and to the registry in `discover`, which are
# different files -- hence one helper, so the two cannot drift apart and leave a station with a parquet
# nothing registers or a registration with no data.
_UID_RE = re.compile(r"^WQ[SP]\d+$")

# The split records the namespace it was cut under. See `_stale`.
_NAMESPACE_STAMP = ".namespace"

# The registry's coordinate fill: -99.0 in BOTH coordinates means "no location", not a point in the
# Gulf of Mexico. A discovery-layer sentinel, distinct from the value sentinels in the registry module.
COORD_SENTINEL = -99.0

_NOT_A_PARAM = {"site_uid", "datetime"}

# Acknowledged gaps live in the registry-gaps LEDGER (`decisions/registry_gaps.csv`), keyed on
# (source, uid) -- the generic decision machinery, not a bespoke file beside the adapter. See `reconcile`.


def _is_uid(values) -> pd.Series:
    """Which of these are IIHR station ids. One rule, two call sites, two different files."""
    return pd.Series(values, dtype="object").astype(str).str.strip().str.upper().str.match(_UID_RE, na=False)


def _registry() -> pd.DataFrame:
    return pd.read_csv(_REGISTRY, dtype=str)


def _chunk_files() -> list:
    return sorted(_CHUNKS.glob("*.csv"))


def _stale() -> bool:
    """True when the split is missing, older than the newest chunk, or cut under a different namespace.

    THE NAMESPACE IS PART OF THE KEY, not just the mtimes. Widening `_UID_RE` changes no file's timestamp, so an mtime-only test returns False, `materialise` returns early, `fetch` finds no parquet for the new stations and every one of them records `no_data` on a build that prints green. Same discipline as `aoe_stamp`: a derived artifact records the rule it was derived under. Narrowing was always safe -- `materialise` sweeps parquets that left the namespace -- so only widening was silent.
    """
    parts = list(_SPLIT.glob("*.parquet"))
    if not parts:
        return True
    stamp = _SPLIT / _NAMESPACE_STAMP
    if not stamp.exists() or stamp.read_text().strip() != _UID_RE.pattern:
        return True
    chunks = _chunk_files()
    if not chunks:
        return False                                   # nothing to rebuild from; keep what exists
    return max(c.stat().st_mtime for c in chunks) > min(p.stat().st_mtime for p in parts)


def materialise(force: bool = False) -> int:
    """Explode the chunk export into one parquet per station. Returns the station count; idempotent.

    `site_uid` is trimmed and upper-cased -- the export pads it -- and `datetime` arrives already parsed by pyarrow, which reads the explicit -06/-05 offsets and normalises to UTC. That parse is what preserves the DST-aware Central-to-UTC conversion; a naive read would put every summer reading an hour out. Duplicate timestamps are KEPT, since the export contains them and deduplicating here would silently disagree with the source.
    """
    import pyarrow as pa
    import pyarrow.csv as pv

    files = _chunk_files()
    if not files:
        raise FileNotFoundError(f"no IWQIS chunk export under {_CHUNKS}")
    if not force and not _stale():
        return len(list(_SPLIT.glob("*.parquet")))

    tabs = [pv.read_csv(f, convert_options=pv.ConvertOptions(column_types={"site_uid": pa.string()}))
            for f in files]
    big = pa.concat_tables(tabs, promote_options="permissive").to_pandas()
    big["site_uid"] = big.site_uid.str.strip().str.upper()
    big = big[_is_uid(big.site_uid).to_numpy()]
    big["datetime"] = pd.to_datetime(big.datetime, utc=True, errors="coerce", format="mixed")
    big = big[big.datetime.notna()]

    _SPLIT.mkdir(parents=True, exist_ok=True)
    for uid, grp in big.groupby("site_uid", sort=True):
        d = grp.drop(columns="site_uid").set_index("datetime").sort_index(kind="stable")
        d.index = d.index.astype("datetime64[us, UTC]")
        bio.write_parquet(d, _SPLIT / f"{uid}.parquet", index=True)
    # HOISTED, and via `.unique()`. As a comprehension condition, `set(big.site_uid)` was rebuilt once per
    # existing parquet -- 152 rebuilds of a set over 43 million strings, which turned a millisecond sweep
    # into tens of minutes AFTER every parquet was already correctly written. `.unique()` does the collapse
    # in C; the set is then 152 elements.
    seen_uids = set(big.site_uid.unique())
    stale = [p for p in _SPLIT.glob("*.parquet") if p.stem not in seen_uids]
    for p in stale:
        p.unlink()
    (_SPLIT / _NAMESPACE_STAMP).write_text(_UID_RE.pattern + "\n")
    return int(big.site_uid.nunique())


def discover(clip=None) -> pd.DataFrame:
    """Every IIHR-registered station, from the registry rather than from what happens to have a parquet.

    THE REGISTRY IS TEXT AND THE TEXT IS READ TWICE: once as numbers for the coordinates, once as strings for `coord_dp_*` -- the declared precision survives only here (IWQIS prints 1-4 decimal places; the 2 dp stations carry ~700 m half-widths). The -99.0/-99.0 coordinate fill is a SENTINEL, not a place: those registrations are KEPT with null coordinates and a recorded reason, participate in no clip, no snap and no proximity -- silently box-dropping them once made six stations unregisterable. `clip` restricts LOCATED rows only; default is the seed boxes (the A1 bootstrap has no AOE yet), and the census passes the AOE mask.
    """
    reconcile()
    r = _registry()
    r = r[_is_uid(r["uid"]).to_numpy()].copy()
    lat = pd.to_numeric(r["latitude"], errors="coerce")
    lon = pd.to_numeric(r["longitude"], errors="coerce")
    dp_lat = base.text_dp(r["latitude"])
    dp_lon = base.text_dp(r["longitude"])
    sentinel = (lat == COORD_SENTINEL) & (lon == COORD_SENTINEL)
    missing_reason = pd.Series(pd.NA, index=r.index, dtype="object")
    missing_reason[sentinel] = f"coordinate_sentinel({COORD_SENTINEL})"
    missing_reason[(lat.isna() | lon.isna()) & ~sentinel] = "no_coordinates_in_registry"
    lat = lat.mask(sentinel)
    lon = lon.mask(sentinel)
    dp_lat = dp_lat.mask(sentinel | lat.isna())
    dp_lon = dp_lon.mask(sentinel | lon.isna())

    # draining_area is reported in square miles, matching the USGS convention it was registered against.
    da = pd.to_numeric(r.get("draining_area"), errors="coerce") * 2.589988

    name = r["nickname"].fillna("").str.strip()
    river = r.get("river", pd.Series([""] * len(r), index=r.index)).fillna("").str.strip()
    town = r.get("town", pd.Series([""] * len(r), index=r.index)).fillna("").str.strip()
    station = _station_name(river, town, name, r["uid"])

    out = pd.DataFrame({
        "source_site_id": r["uid"].astype(str),
        "station_name": station,
        "lat": lat,
        "lon": lon,
        "state": r.get("state", "ia"),
        "county": pd.NA,
        "huc": pd.NA,
        "agency": r.get("operator_uid", pd.Series(["IIHR"] * len(r), index=r.index)).fillna("IIHR").str.upper(),
        "datum": pd.NA,
        "altitude_m": np.nan,
        "source_drainage_area_km2": da,
        # The registry's own words are the only place the network records that a station is a wetland outflow, a tile line or a test logger. Kept verbatim; classification is derived downstream so the raw string stays auditable.
        "site_type_raw": (r.get("description", pd.Series([""] * len(r), index=r.index)).fillna("").str.strip()
                          + " | " + name).str.strip(" |"),
        "regime": "continuous",
        "pcodes": pd.NA,
        "reported_units": "mg/L as N",
        "detection_limit": np.nan,
        "coord_dp_lat": dp_lat,
        "coord_dp_lon": dp_lon,
        "location_missing_reason": missing_reason,
        "tz": pd.NA,
    })
    located = out.lat.notna() & out.lon.notna()
    in_scope = (clip(out) if clip is not None
                else config.in_seed_boxes(lon=out.lon.fillna(0).values, lat=out.lat.fillna(0).values))
    out = out[~located | in_scope]
    return base.conform_discovery(out.reset_index(drop=True), NAME)


def _station_name(river, town, nickname, uid) -> np.ndarray:
    """`place (nickname)`, where place is `river at town` and the parenthetical is dropped when it adds nothing.

    APPENDS RATHER THAN CHOOSES, because the two fields carry different things and only one of them carries a TYPE. `site_type.from_name` is the sole rule that can classify an IWQIS station -- `from_source_field` returns None for every non-USGS source -- so discarding "Saturated Buffer 1" for "Alleman Creek at Alleman" is what makes an engineered treatment structure indistinguishable from the creek beside it. The old rule also dropped `river` outright when `town` was empty, which is how `Benton County DOT Ditch 1` reached the classifier as `105BCD1`.
    """
    out = []
    for rv, tw, nk, u in zip(river, town, nickname, uid):
        rv, tw, nk = str(rv).strip(), str(tw).strip(), str(nk).strip()
        place = f"{rv} at {tw}" if rv and tw else (rv or tw)
        if not place:
            out.append(nk or str(u))
        elif nk and nk.lower() not in place.lower():
            out.append(f"{place} ({nk})")
        else:
            out.append(place)
    return np.array(out, dtype=object)


def reconcile(verbose: bool = True) -> list[str]:
    """Station ids in the EXPORT with no registry row -- observations nothing can reach. Reports; never raises.

    THE TWO SOURCES ARE AUTHORITATIVE FOR DIFFERENT THINGS. The export holds the observations, the registry holds the coordinates, and `schema.validate` requires a lat/lon for every registration -- so a station present only in the export CANNOT be admitted, and the only honest outcome is to name it. `WQS0127` is the live case: present in the 2026-08 export, absent from both the registry and `measures.csv`. Acknowledged gaps live in the ledger at `decisions/registry_gaps.csv`, keyed on (source, uid), and stay quiet; anything else is printed loudly, because a NEW gap means the registry moved and somebody should refetch it.

    Reads the export's `site_uid` column alone, so it costs a fraction of `materialise`.
    """
    import pyarrow as pa
    import pyarrow.csv as pv

    files = _chunk_files()
    if not files:
        return []
    opts = pv.ConvertOptions(column_types={"site_uid": pa.string()}, include_columns=["site_uid"])
    seen = pa.concat_tables([pv.read_csv(f, convert_options=opts) for f in files]).to_pandas()
    uids = seen.site_uid.str.strip().str.upper()
    uids = set(uids[_is_uid(uids).to_numpy()])
    known = set(_registry()["uid"].astype(str).str.strip().str.upper())
    gaps = sorted(uids - known)
    if not gaps:
        return []
    ack = {uid for (src, uid), d in ledger.REGISTRY_GAPS.decisions().items()
           if src == NAME and d == "acknowledged"}
    if verbose:
        fresh = [g for g in gaps if g not in ack]
        print(f"  {len(gaps)} station(s) in the export with no registry row "
              f"({len(gaps) - len(fresh)} acknowledged): {', '.join(gaps)}")
        if fresh:
            print(f"    NOT ACKNOWLEDGED: {', '.join(fresh)} -- the registry has moved; refetch it, or "
                  f"acknowledge the id in {ledger.REGISTRY_GAPS.path.name} with a reason")
    return gaps


def site_parameters(native_id: str) -> list[str]:
    """Column names this station reports. Reads the parquet footer only -- no row is touched."""
    import pyarrow.parquet as pq

    p = _SPLIT / f"{native_id}.parquet"
    if not p.exists():
        return []
    m = pq.ParquetFile(p).metadata.schema
    return [m.column(i).name for i in range(len(m)) if m.column(i).name not in _NOT_A_PARAM]


def fetch(native_id: str, start=None, end=None, channels=None) -> pd.DataFrame | None:
    """All 24 columns at native resolution, from the split of the chunk export. Materialises it first if stale."""
    materialise()
    p = _SPLIT / f"{native_id}.parquet"
    if not p.exists():
        return None
    d = pd.read_parquet(p)
    if "site_uid" in d.columns:
        d = d.drop(columns="site_uid")
    d = base.normalise_obs(d)
    if start is not None:
        d = d[d.index >= pd.Timestamp(start, tz="UTC")]
    if end is not None:
        d = d[d.index <= pd.Timestamp(end, tz="UTC")]
    return d if len(d) else None
