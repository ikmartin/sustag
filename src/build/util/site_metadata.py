"""STEP 1 of the water build: build the CANDIDATE site table, on a USGS-standard schema.

This is the first step of `metadata -> usgs+iwqis -> filter`. It runs BEFORE the data pull and
writes `site_candidates.csv` -- the universe of sites that MIGHT be kept. It does NOT decide the
kept set (that is filter_sites.py, step 3) and it does NOT write the downstream contract file
`site_location_metadata.csv` (filter_sites does, so access.get_metadata() only ever sees the final
kept set).

Because it runs first, it takes its inputs from DIRECT sources, not from usgs.py/iwqis.py outputs:
    USGS candidates  = usgs.USGS_SITE_LIST (26); metadata + coords from get_monitoring_locations API.
    IWQIS candidates = raw site_clean.csv uids matching WQS\\d+ (140; drops junk like JIMTEST).

DESIGN: one canonical schema, one adapter per source. Each adapter returns a frame with the
CANONICAL columns (nulls where it has nothing to say). USGS is the standard because it generalizes
when the project leaves Iowa; IWQIS survives as a namespaced `iwqis_*` supplement.

UNITS, because losing them is how `draining_area` became unreadable:
    latitude / longitude   WGS84 decimal degrees
    drainage_area          SQUARE MILES (both USGS and IWQIS report sq mi)
    altitude               FEET above `vertical_datum`

IWQIS field semantics, recovered by inspection (nothing in the source documents them):
    draining_area   sq mi, integer-rounded, sometimes stale. Sentinel 1 (and one 0) mean "unknown".
    colloc_uid      Postgres array of CO-LOCATED station numbers -- an IWQIS->USGS crosswalk.
    link_uid        external routing-model reach id (IIHR/Iowa Flood Center); not an NHDPlus COMID.
    icao            linked airport station, KDSM-placeholder-dominated -- DROPPED.
    nickname        NWS/AHPS 5-char gage handle; a `\\d+gw` prefix marks a groundwater site.

GROUNDWATER: is_groundwater flags karst springs / wells (Big Spring WQS0031, and USGS wells) that
no surface delineation can represent. filter_sites.py excludes them from the surface set. The flag
must be an OR across site_type + iwqis_river + nickname -- WQS0031's site_type is null, only the
`\\d+gw` nickname catches it.

Output: processed/water/meta/site_candidates.csv (candidate universe, superset schema).
Cache:  processed/water/meta/usgs_monitoring_locations.csv (tracked, so clones build offline).

Usage
-----
    python -m src.build.util.site_metadata [--fetch-only] [--force]
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root on path
from src.build.util._water_paths import build_log_path, meta_dir, raw_dir
from src.build.util.usgs import USGS_SITE_LIST

# ── canonical schema ───────────────────────────────────────────
# Single source of truth for the output. Adapters are reindexed to this, so a source that omits a column yields nulls rather than shifting the schema.
CANONICAL = [
    # contract columns -- the only three anything downstream reads
    "site_uid",
    "latitude",
    "longitude",
    # USGS-standard descriptive fields
    "monitoring_location_name",
    "monitoring_location_number",
    "agency_code",
    "site_type",
    "state_code",
    "state_name",
    "county_name",
    "hydrologic_unit_code",
    "drainage_area",
    "drainage_area_source",
    "altitude",
    "vertical_datum",
    # IWQIS supplement
    "iwqis_uid",
    "iwqis_river",
    "iwqis_town",
    "iwqis_description",
    "iwqis_colloc_uid",
    "iwqis_colloc_site_uid",
    "iwqis_link_uid",
    "iwqis_status",
    "iwqis_nickname",
]

# The candidate table carries one extra column beyond CANONICAL: the groundwater flag, which
# filter_sites.py reads to exclude karst/aquifer sites from the surface set.
CANDIDATE_COLS = CANONICAL + ["is_groundwater"]

# Columns that must survive a CSV round-trip as strings. Without this, 070802051507 becomes
# 70802051507 and 05464500 becomes 5464500 on the SECOND build -- silently, and only then.
STR_COLS = [
    "site_uid",
    "monitoring_location_number",
    "state_code",
    "hydrologic_unit_code",
    "iwqis_uid",
    "iwqis_colloc_uid",
    "iwqis_colloc_site_uid",
]

# Only needed for IWQIS rows, which are Iowa-only. USGS supplies state_code/state_name directly, so
# expansion to new states needs no table here at all.
_STATE_FIPS = {
    "IA": ("19", "Iowa"),
    "NE": ("31", "Nebraska"),
    "MO": ("29", "Missouri"),
    "IL": ("17", "Illinois"),
}

_ML_CACHE_COLS = [
    "monitoring_location_id",
    "monitoring_location_name",
    "monitoring_location_number",
    "agency_code",
    "site_type",
    "state_code",
    "state_name",
    "county_name",
    "hydrologic_unit_code",
    "drainage_area",
    "altitude",
    "vertical_datum",
    "latitude",
    "longitude",
]
_ML_STR_COLS = {
    "monitoring_location_id",
    "monitoring_location_number",
    "hydrologic_unit_code",
    "state_code",
    "county_code",
    "site_type_code",
}


def _ml_cache_path() -> Path:
    return meta_dir() / "usgs_monitoring_locations.csv"


def _candidates_path() -> Path:
    return meta_dir() / "site_candidates.csv"


def _site_clean_path() -> Path:
    return raw_dir() / "site_clean.csv"


# ── helpers ───────────────────────────────────────────────────────────────────


def _parse_pg_array(value) -> list[str]:
    """Postgres array literal -> list of strings. Handles '{05451210}', '{}', NaN.

    Deliberately NOT ast.literal_eval: '{05451210}' is a SyntaxError (leading-zero int literal) and '{}' parses as an empty dict. And never int() -- the leading zero is part of the station number and is what makes 'USGS-' + x reconstruct correctly.
    """
    if not isinstance(value, str):
        return []
    inner = value.strip().lstrip("{").rstrip("}").strip()
    if not inner:
        return []
    return [p.strip().strip('"') for p in inner.split(",") if p.strip().strip('"')]


def _normalize_state(postal) -> tuple[str | None, str | None]:
    """Postal abbreviation -> (FIPS code, full name). Never raises on an unmapped state."""
    if not isinstance(postal, str) or not postal.strip():
        return None, None
    key = postal.strip().upper()
    if key not in _STATE_FIPS:
        print(f"  WARNING: no FIPS mapping for state {key!r}; leaving state_code null.")
        return None, key
    return _STATE_FIPS[key]


def _resolve_drainage_area(usgs_val, iwqis_val) -> tuple[float | None, str | None]:
    """Square miles, USGS preferred. IWQIS sentinels (1, and one 0) become null."""
    if pd.notna(usgs_val):
        return float(usgs_val), "usgs"
    if pd.notna(iwqis_val) and float(iwqis_val) > 1.0:
        return float(iwqis_val), "iwqis"
    return None, None


def _site_clean() -> pd.DataFrame:
    """IWQIS registry with string dtypes pinned (uid mixes WQS0003 and 05481000)."""
    return pd.read_csv(
        _site_clean_path(),
        engine="python",
        on_bad_lines="warn",
        dtype={"uid": str, "colloc_uid": str, "nickname": str, "icao": str},
    )


def _usgs_candidate_ids() -> list[str]:
    """USGS candidates = the curated USGS_SITE_LIST. Kept in usgs.py (tied to its pcode/QA logic), imported here so there is one source of truth. Filtering to the kept set is filter_sites.py."""
    return sorted(USGS_SITE_LIST)


def _iwqis_candidate_ids(clean: pd.DataFrame) -> list[str]:
    """IWQIS candidates = registry uids matching WQS\\d+ (drops junk: JIMTEST, UNKNOWN, bare numeric, USGS-station duplicates). Quality filtering is filter_sites.py."""
    return sorted(clean.loc[clean["uid"].str.match(r"WQS\d+", na=False), "uid"].unique())


# ── USGS monitoring-locations: fetch + cache ──────────────────────────────────


def fetch_monitoring_locations(site_ids: list[str], force: bool = False) -> pd.DataFrame:
    """Site-level USGS metadata, cache-first.

    The cache is git-tracked, so a fresh clone builds with no network. Degrades rather than raising:
    a failed fetch falls back to whatever the cache holds, and callers handle missing rows.
    """
    cache = pd.DataFrame(columns=_ML_CACHE_COLS)
    path = _ml_cache_path()
    if path.exists():
        cache = pd.read_csv(path, dtype={c: str for c in _ML_STR_COLS})

    have = set(cache.get("monitoring_location_id", pd.Series(dtype=str)))
    missing = [s for s in site_ids if s not in have]
    if not missing and not force:
        return cache

    want = site_ids if force else missing
    print(f"  Fetching USGS monitoring-locations for {len(want)} site(s)...")
    try:
        from dataretrieval import waterdata

        fresh = waterdata.get_monitoring_locations(monitoring_location_id=want)
        if isinstance(fresh, tuple):  # some dataretrieval versions return (df, metadata)
            fresh = fresh[0]
        fresh = pd.DataFrame(fresh)
        if "geometry" in fresh.columns:
            fresh["longitude"] = fresh["geometry"].apply(lambda g: getattr(g, "x", None))
            fresh["latitude"] = fresh["geometry"].apply(lambda g: getattr(g, "y", None))
            fresh = fresh.drop(columns=["geometry"])
        keep = [c for c in _ML_CACHE_COLS if c in fresh.columns]
        fresh = fresh[keep].drop_duplicates("monitoring_location_id")
        merged = pd.concat([cache[~cache.monitoring_location_id.isin(fresh.monitoring_location_id)], fresh])
        merged = merged.reindex(columns=_ML_CACHE_COLS).sort_values("monitoring_location_id")
        merged.to_csv(path, index=False)
        print(f"  Cached {len(merged)} site(s) -> {path.name}")
        return merged
    except Exception as e:
        if len(cache):
            print(f"  WARNING: monitoring-locations fetch failed ({e}).")
            print(f"           Falling back to the cache ({len(cache)} site(s)); {len(missing)} will be sparse.")
            return cache
        print(f"  WARNING: monitoring-locations fetch failed ({e}) and no cache exists.")
        print("           USGS descriptive fields will be null; coordinates fall back to the geometry column.")
        return pd.DataFrame(columns=_ML_CACHE_COLS)


# ── source adapters ───────────────────────────────────────────────────────────


def _usgs_rows(site_ids: list[str], ml: pd.DataFrame, clean: pd.DataFrame) -> pd.DataFrame:
    """USGS sites: monitoring-locations for coordinates + descriptive fields, IWQIS as supplement."""
    df = pd.DataFrame({"site_uid": site_ids})
    ml = ml.drop_duplicates("monitoring_location_id").rename(columns={"monitoring_location_id": "site_uid"})
    df = df.merge(ml, on="site_uid", how="left", validate="m:1")
    # Coordinates come from monitoring-locations only (metadata runs before usgs.py writes its
    # geometry file). A site the fetch missed is left null and warned about at the candidate stage.
    for axis in ("latitude", "longitude"):
        if axis not in df.columns:
            df[axis] = pd.NA

    df["agency_code"] = df.get("agency_code", pd.Series(index=df.index, dtype=object)).fillna("USGS")

    # 19 of 24 are also IWQIS-registered; join on the bare station number.
    clean = clean.copy()
    clean["_join"] = clean["uid"]
    df["_join"] = df["site_uid"].str.removeprefix("USGS-")
    df = df.merge(clean, on="_join", how="left", suffixes=("", "_iw"), validate="m:1").drop(columns=["_join"])

    return _apply_iwqis_supplement(df)


def _iwqis_rows(site_ids: list[str], ml: pd.DataFrame, clean: pd.DataFrame) -> pd.DataFrame:
    """IWQIS sites. monitoring_location_name stays NULL by decision -- never synthesized."""
    df = clean[clean["uid"].isin(site_ids)].copy()
    df["site_uid"] = df["uid"]
    states = df["state"].apply(_normalize_state)
    df["state_code"] = [s[0] for s in states]
    df["state_name"] = [s[1] for s in states]
    df["agency_code"] = df["operator_uid"].astype(str).str.upper().replace({"NAN": None})
    return _apply_iwqis_supplement(df)


def _apply_iwqis_supplement(df: pd.DataFrame) -> pd.DataFrame:
    """Map the IWQIS columns into their iwqis_* homes and resolve drainage_area. Shared by both adapters, since USGS rows can also carry an IWQIS registration."""
    df = df.copy()
    df["iwqis_uid"] = df.get("uid")
    for src, dst in [
        ("river", "iwqis_river"),
        ("town", "iwqis_town"),
        ("description", "iwqis_description"),
        ("status", "iwqis_status"),
        ("nickname", "iwqis_nickname"),
    ]:
        df[dst] = df.get(src)

    df["iwqis_colloc_uid"] = df.get("colloc_uid", pd.Series(index=df.index, dtype=object)).apply(
        lambda v: ",".join(_parse_pg_array(v)) or None
    )
    df["iwqis_link_uid"] = pd.to_numeric(df.get("link_uid"), errors="coerce").astype("Int64")

    resolved = [
        _resolve_drainage_area(u, i)
        for u, i in zip(
            df.get("drainage_area", pd.Series(index=df.index, dtype=float)),
            df.get("draining_area", pd.Series(index=df.index, dtype=float)),
        )
    ]
    df["drainage_area"] = [r[0] for r in resolved]
    df["drainage_area_source"] = [r[1] for r in resolved]
    return df


SOURCES = [_usgs_rows, _iwqis_rows]


def _groundwater_flag(df: pd.DataFrame) -> pd.Series:
    """True for karst-spring / well sites, which no surface delineation can represent.

    OR across three signals -- must be all three: the USGS well is caught by site_type, but Big Spring (WQS0031) has a NULL site_type and is caught only by its `31gw...` nickname.
    """
    site_type = df["site_type"].astype(str).str.strip().str.lower()
    river = df["iwqis_river"].astype(str).str.lower()
    nick = df["iwqis_nickname"].astype(str)
    return (
        site_type.isin(["well", "spring"])
        | river.str.contains("groundwater", na=False)
        | river.str.contains("spring", na=False)
        | nick.str.match(r"\d+gw\w*", na=False)
    )


# ── build ─────────────────────────────────────────────────────────────────────


def _resolve_colloc(df: pd.DataFrame) -> pd.Series:
    """Co-located station numbers -> a site_uid that actually exists in this table.

    Makes the physically-duplicated pairs (WQS0081/USGS-05481000 and friends) machine-readable.
    """
    known = set(df["site_uid"])
    bare = {u.removeprefix("USGS-"): u for u in known}

    def resolve(row):
        for member in _parse_pg_array(row.get("_colloc_raw")):
            for candidate in (member, f"USGS-{member}", bare.get(member)):
                if candidate and candidate in known and candidate != row["site_uid"]:
                    return candidate
        return None

    return df.apply(resolve, axis=1)


def build_candidates(force: bool = False) -> pd.DataFrame:
    """STEP 1: write site_candidates.csv (the candidate universe). Returns the frame.

    Runs first, off direct sources. Does NOT write the contract file or clear the access cache -- that is filter_sites.py after the kept set is known.
    """
    clean = _site_clean()
    usgs_ids = _usgs_candidate_ids()
    iwqis_ids = _iwqis_candidate_ids(clean)
    ml = fetch_monitoring_locations(usgs_ids, force=force)

    blocks = []
    for adapter in SOURCES:
        ids = usgs_ids if adapter is _usgs_rows else iwqis_ids
        block = adapter(ids, ml, clean)
        block["_colloc_raw"] = block.get("colloc_uid")
        blocks.append(block)

    # ROW ORDER IS LOAD-BEARING downstream (holdout_split inherits CSV order). Sort by site_uid so
    # the ordering is deterministic and independent of source count. filter_sites re-sorts the final
    # file too, but sorting here keeps the candidate table diff-stable.
    out = pd.concat(blocks, ignore_index=True).sort_values("site_uid", ignore_index=True)
    out["iwqis_colloc_site_uid"] = _resolve_colloc(out)
    out["is_groundwater"] = _groundwater_flag(out)
    out = out.reindex(columns=CANDIDATE_COLS + ["_colloc_raw"]).drop(columns=["_colloc_raw"])

    for c in STR_COLS:
        out[c] = out[c].astype("object").where(out[c].notna(), None)

    expected = set(usgs_ids) | set(iwqis_ids)
    assert set(out.site_uid) == expected, f"candidate set drifted: {expected ^ set(out.site_uid)}"
    assert len(out) == out.site_uid.nunique() == len(usgs_ids) + len(iwqis_ids), "duplicate site_uid"

    # Candidate-stage coordinate check is a WARNING, not fatal -- the raw registry may carry a
    # coordinate-less candidate that filtering will drop anyway. filter_sites enforces the hard check
    # on the final kept set.
    missing = out[out[["latitude", "longitude"]].isna().any(axis=1)]
    if len(missing):
        print(f"  WARNING: {len(missing)} candidate(s) have no coordinates: {', '.join(missing.site_uid)}")

    out.to_csv(_candidates_path(), index=False)
    n_gw = int(out.is_groundwater.sum())
    print(
        f"site_candidates.csv: {len(out)} candidates ({len(usgs_ids)} USGS + {len(iwqis_ids)} IWQIS), "
        f"{n_gw} flagged groundwater, {int(out.monitoring_location_name.notna().sum())} named"
    )

    # Start the build log fresh (step 1); filter_sites appends the funnel (step 3).
    gw_ids = ", ".join(out.loc[out.is_groundwater, "site_uid"])
    with open(build_log_path(), "w") as f:
        f.write("=== Water build ===\n\n")
        f.write("[1] Candidates\n")
        f.write(f"  {len(out)} candidates: {len(usgs_ids)} USGS + {len(iwqis_ids)} IWQIS (WQS####)\n")
        f.write(f"  groundwater-flagged: {n_gw}" + (f" ({gw_ids})" if n_gw else "") + "\n")
        if len(missing):
            f.write(f"  WARNING no coordinates: {', '.join(missing.site_uid)}\n")
        f.write("\n")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fetch-only", action="store_true", help="Populate the monitoring-locations cache and stop.")
    parser.add_argument("--force", action="store_true", help="Re-fetch every site, ignoring the cache.")
    args = parser.parse_args()
    if args.fetch_only:
        fetch_monitoring_locations(_usgs_candidate_ids(), force=args.force)
    else:
        build_candidates(force=args.force)
        print(f"Wrote {_candidates_path()}")
