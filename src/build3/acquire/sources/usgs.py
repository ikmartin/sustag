"""USGS continuous nitrate, via the OGC waterdata API.

DISCOVERY USES ALL THREE NITRATE PCODES. USGS codes continuous nitrate under 99133, 99137 and 00630, and which one a network files under is a local convention rather than a property of the data: a 99133-only query returns ZERO Nebraska sites while 00630 returns five. Querying one code does not thin a network, it deletes it. `83554` is excluded on purpose -- it is a load, not a concentration.

FETCH TAKES EVERY PARAMETER THE SITE REPORTS. The old build pulled a fixed 8-code `CORE_PCODES` set, which is why turbidity, chlorophyll and the rest were paid for at some sites and absent at others. Here the parameter list comes from the site's own time-series metadata.

WHAT ACTUALLY CONSTRAINS THE FETCH, measured rather than inherited. There is ONE server limit: a request's time envelope may not exceed 1100 days, whatever the parameter count. There is NO row cap -- a single call returned 927,854 rows -- so the old build's 50k-row budget, and the narrow windows it computed from parameter count, were solving a problem that does not exist. Cost is throughput, not round trips: batching 28 calls into 14 saved 5%, while fetching the same 14 concurrently cut 325s to 79s.

So the shape is: cut the span to `_MAX_SPAN`, ask for every parameter at once, and run the windows `_CONCURRENT` at a time. Quota is per token and recovers, so exhaustion rotates to the next key in `api-keys.toml` instead of stopping.
"""

from __future__ import annotations

import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from ... import config, io as bio
from . import base

NAME = "USGS"

NITRATE_PCODES = ("99133", "99137", "00630")   # 83554 is a LOAD -- deliberately excluded
_GRID = "15min"
# A HARD SERVER LIMIT, not a tuning choice. The API rejects any request whose time envelope exceeds 1100 days with `400 InvalidQuery`, whatever the parameter count or row volume, so a full-window request cannot work and the span must be cut before asking. 1000 leaves margin.
_MAX_SPAN = pd.Timedelta(days=1000)
_RETRIES, _PAUSE = 3, 5.0          # our own outer retry, exponential from _PAUSE
_LIB_RETRIES = 6                   # API_USGS_RETRIES: the library's per-sub-request backoff
# THE ONLY CONCURRENCY KNOB IN THE FETCH: how many 1000-day windows `_native` requests at once. The site loop is serial and the library's own fan-out is pinned to 1 below, so this number IS the total simultaneous request count -- there is deliberately no second multiplier. The design it replaced had two, 6 site workers over a library default of 32, which reached roughly 190 concurrent requests and lost 194 sites to HTTP 429.
_CONCURRENT = 8


_TOKENS: list[str] = []
_TOK_IDX = 0
_TOK_LOCK = threading.Lock()


_PINNED = False


def use_token(index: int) -> int:
    """Pin this PROCESS to one key and stop rotating. Returns the index in force.

    THE POINT OF SHARDING. `dataretrieval` reads the key from `os.environ["API_USGS_PAT"]`, one value per process, so two threads cannot hold different keys -- whichever set it last wins for both. That is why nine keys currently buy nine times the QUOTA and none of the rate: rotation only fires when a key is spent. Give each of nine processes its own key and the rate limits, which are per key, become independent.

    Rotation is DISABLED once pinned. A shard that rotated on exhaustion would start spending a key another shard is actively using, and the two would then throttle each other while both believing they had a budget. Exhaustion here backs off and waits for the key to recover, which is the correct behaviour when the key is genuinely yours.
    """
    global _TOK_IDX, _PINNED
    if not _TOKENS:
        _TOKENS.extend(config.usgs_pats() or [config.usgs_pat()])
    _TOK_IDX = index % len(_TOKENS)
    _PINNED = True
    os.environ["API_USGS_PAT"] = _TOKENS[_TOK_IDX]
    os.environ["API_USGS_CONCURRENT"] = "1"
    os.environ.setdefault("API_USGS_RETRIES", str(_LIB_RETRIES))
    return _TOK_IDX


def n_tokens() -> int:
    """How many keys are on file -- the natural upper bound on shard count."""
    if not _TOKENS:
        _TOKENS.extend(config.usgs_pats() or [config.usgs_pat()])
    return len(_TOKENS)


def _rotate_token(seen_idx: int) -> int:
    """Move to the next token after a quota error. Returns the index now in force.

    Guarded and idempotent per exhaustion. Eight windows run concurrently and a spent token fails all of them at once, so without the `seen_idx` check the first failure would advance the rotation and the other seven would advance it again -- skipping past good tokens and, with two keys, landing straight back on the exhausted one. A thread that reports an index older than the current one has already been rescued by whoever rotated first.
    """
    global _TOK_IDX
    if _PINNED:
        return _TOK_IDX                      # this key is ours alone; back off rather than take another's
    with _TOK_LOCK:
        if seen_idx == _TOK_IDX and len(_TOKENS) > 1:
            _TOK_IDX = (_TOK_IDX + 1) % len(_TOKENS)
            os.environ["API_USGS_PAT"] = _TOKENS[_TOK_IDX]
        return _TOK_IDX


def _auth() -> None:
    """Token plus the library's own concurrency cap.

    `API_USGS_CONCURRENT` is pinned to 1 ON PURPOSE. The library fans a call out into concurrent sub-requests, 32 deep by default, and `_native` already parallelises across windows -- leaving both in play multiplies them, which is exactly how the first cohort run reached ~190 simultaneous requests and lost 194 sites to HTTP 429. Every request this module sends is inside the server's 1100-day envelope, so there is nothing left for the library to split and nothing is given up. Its retry budget is raised instead, since the rate limit is on volume over time and backing off correctly matters more than issuing more requests.
    """
    global _TOKENS
    if not _TOKENS:
        _TOKENS = config.usgs_pats() or [config.usgs_pat()]
    if _PINNED:
        return                               # `use_token` already set this process's key
    os.environ["API_USGS_PAT"] = _TOKENS[_TOK_IDX]
    os.environ["API_USGS_CONCURRENT"] = "1"
    os.environ.setdefault("API_USGS_RETRIES", str(_LIB_RETRIES))


def _wd():
    from dataretrieval import waterdata

    return waterdata


def _windows(start, end, step=_MAX_SPAN):
    """Half-open windows no wider than the API's time envelope. Endpoints are shared and deduplicated by the caller."""
    cur, end = pd.Timestamp(start), pd.Timestamp(end)
    while cur < end:
        nxt = min(cur + step, end)
        yield cur, nxt
        cur = nxt


def _with_rotation(call):
    """Run `call()` under token rotation and exponential backoff. Re-raises once every option is spent.

    EVERY CALL THAT TOUCHES THE API GOES THROUGH HERE, metadata included. The first version guarded only the data fetch, so `site_parameters` hit the exhausted token directly, raised before any rotation could happen, and the whole cohort failed `after 0/1 sub-requests` in under a minute -- rotation was never reached because the request that discovers the parameters comes first.

    A quota error rotates and retries immediately WITHOUT consuming a retry: a fresh token is a different request, not another attempt at the same one. Rotation is bounded to one pass through the ring, otherwise two spent tokens would alternate forever and never sleep; after a backoff the count resets, since by then the first token may have recovered.
    """
    attempt, rotations = 0, 0
    while True:
        idx = _TOK_IDX
        try:
            return call()
        except Exception as e:
            if (type(e).__name__ == "QuotaExhausted" and rotations < len(_TOKENS) - 1
                    and _rotate_token(idx) != idx):
                rotations += 1
                continue
            attempt += 1
            if attempt > _RETRIES:
                raise
            time.sleep(_PAUSE * (2 ** (attempt - 1)))
            rotations = 0


def _request(native_id: str, pcodes: list[str], lo, hi):
    """One windowed request. Returns the frame, a partial frame, or None; never raises.

    Swallowing is right HERE and nowhere else: windows are independent, so a lost one costs its own days and leaves a visible gap in `longest_gap_d`, whereas a lost metadata call would silently turn a real site into `no_data`.
    """
    try:
        res = _with_rotation(lambda: _wd().get_continuous(
            monitoring_location_id=f"USGS-{native_id}", parameter_code=pcodes,
            time=[lo.isoformat(), hi.isoformat()]))
        d = res[0] if isinstance(res, tuple) else res
        return d if d is not None and len(d) else None
    except Exception as e:
        partial = getattr(e, "partial_frame", None)
        return partial if partial is not None and len(partial) else None


# THE DAILY SERVICE TAKES MANY SITES PER REQUEST, and that is the difference between a twenty-minute pass
# and a two-hour one. Measured: one site per call costs 0.43 s, so 13,331 of them is 1.6 hours; 300 sites in
# one call costs 0.085 s per site, about 20 minutes for the same set. The site loop stays serial and per-site
# -- error attribution and resumability depend on it -- so the batching happens HERE, filling a cache the
# loop then reads. Nothing else about `records.main` changes.
_DV_BATCH = 300
_DV_CACHE: dict[str, pd.DataFrame] = {}
_DV_FETCHED: set[str] = set()


def _shape_dv(d: pd.DataFrame) -> pd.DataFrame | None:
    """One site's raw `dv` response -> a UTC-indexed frame with bare pcode columns."""
    tcol = next((c for c in d.columns if "datetime" in c.lower() or c.lower() == "date"), None)
    if tcol is None:
        return None
    keep = {c: c.split("_")[0] for c in d.columns if re.match(r"^\d{5}(_00003)?$", c)}
    if not keep:
        keep = {c: c.split("_")[0] for c in d.columns if re.match(r"^\d{5}_", c) and "_cd" not in c}
    if not keep:
        return None
    out = d[[tcol] + list(keep)].rename(columns={**keep, tcol: "datetime"})
    # A site may publish several series for one parameter -- different sub-locations, methods or stat codes -- and stripping the suffix collapses them onto one name. Left as-is that is a frame with repeated column labels, which parquet refuses to write and which killed 194 sites of the first cohort run. Collapse by row-wise max, the same rule the native pivot uses.
    if out.columns.duplicated().any():
        out = out.T.groupby(level=0).max().T
    return base.normalise_obs(out)


def prefetch_dv(native_ids, start, end, pcodes: list[str] | None = None,
                verbose: bool = True) -> int:
    """Batch-fill the daily-value cache for many sites. Returns the number of sites that returned data.

    Requests the WHOLE registry pcode set per batch rather than each site's own: the response only contains what a site actually publishes, so asking for more costs nothing, and one pcode list is what makes a batch a batch. A failed batch is bisected, since the service answers a bad site number with an error for the entire request.
    """
    from dataretrieval import nwis

    from ... import registry

    codes = list(pcodes or registry.usgs_pcodes())
    todo = [i for i in dict.fromkeys(native_ids) if i not in _DV_FETCHED]
    queue = [todo[i:i + _DV_BATCH] for i in range(0, len(todo), _DV_BATCH)]
    got, done, n_batches = 0, 0, len(queue)
    while queue:
        chunk = queue.pop(0)
        try:
            d, _ = nwis.get_dv(sites=chunk, parameterCd=codes, statCd="00003",
                               start=str(pd.Timestamp(start).date()), end=str(pd.Timestamp(end).date()))
        except Exception:
            if len(chunk) == 1:
                _DV_FETCHED.add(chunk[0])           # asked for, nothing came back
                continue
            mid = len(chunk) // 2
            queue[:0] = [chunk[:mid], chunk[mid:]]
            continue
        _DV_FETCHED.update(chunk)
        done += 1
        if verbose:
            print(f"      batch {done}/{n_batches}  {len(_DV_FETCHED):,} site(s) asked, "
                  f"{got:,} with data", flush=True)
        if d is None or not len(d):
            continue
        d = d.reset_index()
        if "site_no" not in d.columns:
            continue
        for sid, grp in d.groupby("site_no"):
            shaped = _shape_dv(grp.drop(columns="site_no"))
            if shaped is not None and len(shaped):
                _DV_CACHE[str(sid)] = shaped
                got += 1
    return got


def _daily_values(native_id: str, pcodes: list[str], start, end) -> pd.DataFrame | None:
    """Daily statistics for the non-nitrate parameters, from the legacy NWIS `dv` service.

    One row per parameter-day instead of 96 collapses an 18-year pull from ~150 requests to about two.
    """
    if not pcodes:
        return None
    from dataretrieval import nwis

    frames = []
    for stat in ("00003",):                       # daily mean; max/min exist but mean is the series
        try:
            d, _ = nwis.get_dv(sites=native_id, parameterCd=pcodes, statCd=stat,
                               start=str(start.date()), end=str(end.date()))
        except Exception:
            continue
        if d is not None and len(d):
            frames.append(d.reset_index())
    if not frames:
        return None
    d = pd.concat(frames, ignore_index=True)
    tcol = next((c for c in d.columns if "datetime" in c.lower() or c.lower() == "date"), None)
    if tcol is None:
        return None
    keep = {c: c.split("_")[0] for c in d.columns if re.match(r"^\d{5}(_00003)?$", c)}
    if not keep:
        keep = {c: c.split("_")[0] for c in d.columns if re.match(r"^\d{5}_", c) and "_cd" not in c}
    if not keep:
        return None
    out = d[[tcol] + list(keep)].rename(columns={**keep, tcol: "datetime"})
    # A site may publish several series for one parameter -- different sub-locations, methods or stat codes -- and stripping the suffix collapses them onto one name. Left as-is that is a frame with repeated column labels, which parquet refuses to write and which killed 194 sites of the first cohort run. Collapse by row-wise max, the same rule the native pivot uses.
    if out.columns.duplicated().any():
        out = out.T.groupby(level=0).max().T
    return base.normalise_obs(out)


def _series_metadata(**kw) -> pd.DataFrame:
    """Time-series metadata, under rotation. RAISES when the quota is spent, deliberately.

    An empty frame here is indistinguishable from a site that reports nothing, and `fetch` would turn it into `no_data` -- a permanent-looking verdict on a site that was merely rate-limited. Raising records it as an error instead, so the next run retries it.
    """
    res = _with_rotation(lambda: _wd().get_time_series_metadata(**kw))
    df = res[0] if isinstance(res, tuple) else res
    return df if df is not None else pd.DataFrame()


# How many monitoring-location ids go in one metadata request. A whole census in a single call is a URL of
# a quarter-million characters, which the server rejects outright -- and rejects as ONE failure, taking every
# site with it. Chunked, a bad response costs its own batch.
_ML_BATCH = 400


def _monitoring_locations(ids: list[str]) -> pd.DataFrame:
    """Station metadata for many ids, in batches, concatenated.

    A failed batch is BISECTED rather than abandoned: the API answers a malformed id with HTTP 400 for the whole request, so one bad entry in four hundred would otherwise cost all four hundred. Halving isolates it in about nine requests and keeps the rest.
    """
    def fetch(chunk: list[str]) -> pd.DataFrame:
        res = _with_rotation(lambda: _wd().get_monitoring_locations(monitoring_location_id=chunk))
        d = res[0] if isinstance(res, tuple) else res
        return d if d is not None else pd.DataFrame()

    out, queue = [], [ids[i:i + _ML_BATCH] for i in range(0, len(ids), _ML_BATCH)]
    while queue:
        chunk = queue.pop(0)
        try:
            out.append(fetch(chunk))
        except Exception:
            if len(chunk) == 1:
                print(f"    dropped monitoring location {chunk[0]}: metadata request failed")
                continue
            mid = len(chunk) // 2
            queue[:0] = [chunk[:mid], chunk[mid:]]
    frames = [d for d in out if len(d)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _tier_pcodes(channels: str | None) -> list[str]:
    """USGS pcodes behind a declared channel set; the whole registry when nothing is declared."""
    from ... import registry

    # pd.isna FIRST: `not pd.NA` raises before the NaN identity check can run, and `pd.NA != pd.NA` is
    # itself NA -- the census carries declared-nothing sensors, so this guard sees NA on every such fetch.
    if channels is None or pd.isna(channels) or not str(channels).strip():
        return list(registry.usgs_pcodes())
    want = set(str(channels).split("+"))
    out = []
    for name in want:
        ch = registry.CHANNELS.get(name)
        sm = ch.sources.get("USGS") if ch else None
        if sm:
            out += [c for c in sm.cols if c not in out]
    return out or list(registry.usgs_pcodes())


def _valid_id(mlid: str) -> bool:
    """True for a USGS site number this API will accept.

    ONE BAD ID RETURNS HTTP 400 FOR THE WHOLE BATCH, so this is not cosmetic. The census returns monitoring locations from other agencies too -- `AR008-`, `IL004-`, `USCE-`, `VA087-` prefixes, ten of 15,752 measured -- and a single one of those in a 300-site request loses all 300.
    """
    return mlid.startswith("USGS-") and mlid[5:].isdigit()


def census_pcodes() -> tuple[str, ...]:
    """Every parameter code the build collects, from the channel registry. Nitrate codes lead."""
    from ... import registry as params

    return params.usgs_pcodes()


def discover(census: bool = True, cover=None, clip=None) -> pd.DataFrame:
    """Every site in the seed boxes reporting any channel the build collects.

    TWO PASSES OVER ONE MECHANISM. The nitrate pcodes are queried first and mark their sites `census_source='nitrate'`; the remaining registry pcodes then add every other continuous sensor as `census_source='census'`. The distinction is worth keeping because it is the difference between a site that can be trained on and a site that can only inform its neighbours, and it is free -- the metadata response already says which parameters a location publishes.

    Queried per SEED box and unioned: the corn-belt and Mississippi boxes overlap, so a site there is returned twice, and `in_seed_boxes` clips afterwards because the API honours a bbox loosely.

    STILL THE SEED BOXES, NOT THE AOE. The AOE is derived from these sensors' basins, so discovering nitrate against it would be circular. The census could legitimately use the AOE -- it never feeds back into the extent -- but that widens the pull into the Missouri headwaters for graph context a corn-belt model will not use, so the wider scope stays available and unused.

    `census=False` restores the nitrate-only behaviour, which is what makes the census reviewable against the cohort that preceded it.
    """
    from ... import registry as params

    _auth()
    want = NITRATE_PCODES if not census else census_pcodes()
    frames = []
    for bx in (cover if cover is not None else list(config.seed_boxes().values())):
        for pc in want:
            d = _series_metadata(parameter_code=pc, bbox=list(bx), skip_geometry=True)
            if len(d):
                frames.append(d.assign(_pcode=pc))
    if not frames:
        return base.conform_discovery(pd.DataFrame(), NAME)
    a = pd.concat(frames, ignore_index=True)
    a["monitoring_location_id"] = a.monitoring_location_id.astype(str)

    # Non-USGS agencies appear in the response and this API cannot fetch them. Dropped HERE rather than at
    # fetch time, so the count reported by discovery is the count that can actually be collected.
    n_all = a.monitoring_location_id.nunique()
    a = a[a.monitoring_location_id.map(_valid_id)]
    dropped = n_all - a.monitoring_location_id.nunique()
    if dropped:
        print(f"    dropped {dropped} non-USGS monitoring location(s) the API cannot fetch")

    # Canonical channels per site, and which pass found it.
    ch_of = {}
    for pc in want:
        for name, chan in params.CHANNELS.items():
            sm = chan.sources.get("USGS")
            if sm and pc in sm.cols:
                ch_of[pc] = name
    per_site = (a.groupby("monitoring_location_id")._pcode
                .apply(lambda s: sorted({ch_of[p] for p in s if p in ch_of})))
    declared = per_site.apply(lambda v: "+".join(v) if v else pd.NA)
    origin = per_site.apply(lambda v: "nitrate" if "nitrate" in v else "census")

    # The advertised nitrate span rides along for free -- this response already carries begin/end per series, so the seed filter needs no extra request per site. A site registered for nitrate with NaT on both (05374900, 01161280) has no series at all, which is a different thing from a short one.
    for c in ("begin", "end"):
        if c in a.columns:
            a[c] = pd.to_datetime(a[c], errors="coerce", utc=True)

    # `pcodes`, `reported_units` and the span are NITRATE-SPECIFIC by contract, so they aggregate over the
    # nitrate rows alone. Taking them over every parameter would make `nitrate_begin` the earliest date of
    # ANY series -- typically a discharge record decades older -- and the seed filter reads that column.
    # The ANY-CHANNEL declared span, over every registry-mapped row -- the fetch screen's evidence.
    mapped = a[a._pcode.isin(set(ch_of))]
    if "begin" in mapped.columns and len(mapped):
        span = (mapped.groupby("monitoring_location_id")
                .agg(declared_begin=("begin", "min"), declared_end=("end", "max")))
        for c in ("declared_begin", "declared_end"):
            span[c] = span[c].dt.strftime("%Y-%m-%d")
    else:
        span = pd.DataFrame(columns=["declared_begin", "declared_end"])

    nit = a[a._pcode.isin(NITRATE_PCODES)]
    agg = dict(pcodes=("_pcode", lambda s: "+".join(sorted(set(s)))),
               units=("unit_of_measure", lambda s: "+".join(sorted({str(x) for x in s if pd.notna(x)}))))
    if "begin" in nit.columns:
        agg["nitrate_begin"] = ("begin", "min")
    if "end" in nit.columns:
        agg["nitrate_end"] = ("end", "max")
    nagg = (nit.groupby("monitoring_location_id").agg(**agg)
            if len(nit) else pd.DataFrame(columns=list(agg)))
    for c in ("nitrate_begin", "nitrate_end"):
        if c in nagg.columns:
            nagg[c] = nagg[c].dt.strftime("%Y-%m-%d")

    per = pd.DataFrame({"monitoring_location_id": per_site.index})
    per["channels_declared"] = per.monitoring_location_id.map(declared)
    per["census_source"] = per.monitoring_location_id.map(origin)
    per = per.join(nagg, on="monitoring_location_id").join(span, on="monitoring_location_id")
    for c in ("pcodes", "units", "nitrate_begin", "nitrate_end"):
        if c not in per.columns:
            per[c] = pd.NA

    ml = _monitoring_locations(per.monitoring_location_id.tolist())
    ml = ml[0] if isinstance(ml, tuple) else ml
    ml["monitoring_location_id"] = ml.monitoring_location_id.astype(str)
    # Coordinates live in a POINT geometry, not lat/lon columns. Extract them HERE: merging a GeoDataFrame into a plain frame demotes it, and the geometry column loses its .x/.y accessor. Verified for this endpoint: all Point, no Z, no nulls -- so x/y as two floats is lossless. The response declares NO CRS (`ml.crs is None`); WGS84 degrees is asserted from the value ranges, not read from metadata. Safe here, but it is our assumption, not the API's promise.
    if "geometry" in ml.columns:
        ml = ml.assign(_lat=ml.geometry.y.to_numpy(), _lon=ml.geometry.x.to_numpy()).drop(columns="geometry")
    m = per.merge(ml, on="monitoring_location_id", how="left", suffixes=("", "_ml"))

    def col(*names):
        for n in names:
            if n in m.columns:
                return m[n]
        return pd.Series([None] * len(m))

    # drainage_area is square miles in the USGS schema; the contract stores km2.
    da = pd.to_numeric(col("drainage_area", "drain_area_va"), errors="coerce") * 2.589988
    alt = pd.to_numeric(col("altitude"), errors="coerce") * 0.3048          # feet -> metres

    lat = pd.to_numeric(col("_lat", "latitude"), errors="coerce")
    lon = pd.to_numeric(col("_lon", "longitude"), errors="coerce")

    out = pd.DataFrame({
        "source_site_id": m.monitoring_location_id.str.replace("^USGS-", "", regex=True),
        "station_name": col("monitoring_location_name", "station_nm"),
        "lat": lat,
        "lon": lon,
        "tz": _tz_from_usgs(col("time_zone_abbreviation"), col("uses_daylight_savings")),
        "state": col("state_name", "state_code"),
        "county": col("county_name"),
        "huc": col("hydrologic_unit_code").astype("string"),
        "agency": col("agency_code").fillna("USGS"),
        "datum": col("vertical_datum", "dec_coord_datum_cd"),
        "altitude_m": alt,
        "source_drainage_area_km2": da,
        "site_type_raw": col("site_type", "site_type_code"),
        "regime": "continuous",
        "pcodes": m.pcodes,
        "reported_units": m.units.replace("", pd.NA),
        "detection_limit": np.nan,
        "nitrate_begin": col("nitrate_begin"),
        "nitrate_end": col("nitrate_end"),
        "channels_declared": m.channels_declared,
        "census_source": m.census_source,
        "declared_begin": m.get("declared_begin"),
        "declared_end": m.get("declared_end"),
    })
    out["state"] = out.state.map(_STATE_ABBR).fillna(out.state)
    # The API honours a bbox loosely, so results are clipped after. Default: the seed boxes. A2 passes an
    # AOE-contains clip instead -- the seed-box default would cut the extent's arms off the census.
    mask = (clip(out) if clip is not None
            else config.in_seed_boxes(lon=out.lon.values, lat=out.lat.values))
    out = out[mask]
    return base.conform_discovery(out.reset_index(drop=True), NAME)


def register_one(native_id: str) -> pd.DataFrame:
    """One site's discovery row by DIRECT lookup -- for canary probes the pcode sweep can never return.

    The census discovers by the registry's parameter codes, so a groundwater-level well or a precipitation station is structurally invisible to it -- which is precisely the class the canary roster exists to keep exercised. This asks for the location and its parameters by id, maps whatever registry channels it does carry (often none), and returns a conformed one-row frame with `census_source='canary'`. Empty frame when the id is unknown to the API -- the caller reports a roster problem, never invents a row.
    """
    from ... import registry as params

    _auth()
    ml = _monitoring_locations([f"USGS-{native_id}"])
    ml = ml[0] if isinstance(ml, tuple) else ml
    if ml is None or not len(ml):
        return base.conform_discovery(pd.DataFrame(), NAME)
    ml = ml.iloc[[0]].copy()
    if "geometry" in ml.columns:
        ml = ml.assign(_lat=ml.geometry.y.to_numpy(), _lon=ml.geometry.x.to_numpy()).drop(columns="geometry")

    pars = site_parameters(native_id)
    codes = sorted(set(pars.parameter_code.astype(str))) if len(pars) else []
    chans = sorted({ch for pc in codes for ch, _ in [params.resolve(NAME, pc)] if ch})

    def col(*names):
        for n in names:
            if n in ml.columns:
                return ml[n]
        return pd.Series([None])

    da = pd.to_numeric(col("drainage_area", "drain_area_va"), errors="coerce") * 2.589988
    out = pd.DataFrame({
        "source_site_id": [native_id],
        "station_name": col("monitoring_location_name", "station_nm").values,
        "lat": pd.to_numeric(col("_lat", "latitude"), errors="coerce").values,
        "lon": pd.to_numeric(col("_lon", "longitude"), errors="coerce").values,
        "tz": _tz_from_usgs(col("time_zone_abbreviation"), col("uses_daylight_savings")).values,
        "state": col("state_name", "state_code").values,
        "county": col("county_name").values,
        "huc": col("hydrologic_unit_code").astype("string").values,
        "agency": col("agency_code").fillna("USGS").values,
        "datum": col("vertical_datum", "dec_coord_datum_cd").values,
        "altitude_m": (pd.to_numeric(col("altitude"), errors="coerce") * 0.3048).values,
        "source_drainage_area_km2": da.values,
        "site_type_raw": col("site_type", "site_type_code").values,
        "regime": "continuous",
        "pcodes": ["+".join(codes) if codes else pd.NA],
        "reported_units": pd.NA,
        "detection_limit": np.nan,
        "nitrate_begin": pd.NA, "nitrate_end": pd.NA,
        "channels_declared": ["+".join(chans) if chans else pd.NA],
        "census_source": "canary",
    })
    out["state"] = out.state.map(_STATE_ABBR).fillna(out.state)
    return base.conform_discovery(out.reset_index(drop=True), NAME)


def site_parameters(native_id: str) -> pd.DataFrame:
    """Every continuous parameter this site reports -- the basis for fetching all of them."""
    _auth()
    d = _series_metadata(monitoring_location_id=f"USGS-{native_id}", skip_geometry=True)
    if not len(d):
        return pd.DataFrame(columns=["parameter_code", "parameter_name", "unit_of_measure", "begin", "end"])
    keep = [c for c in ("parameter_code", "parameter_name", "unit_of_measure", "begin", "end") if c in d.columns]
    return d[keep].drop_duplicates("parameter_code").reset_index(drop=True)


def _native(native_id: str, pcodes: list[str], start, end) -> pd.DataFrame | None:
    """Native-resolution pull for one parameter set over the whole window, pivoted long -> wide.

    WINDOWED BECAUSE THE SERVER DEMANDS IT. The API refuses any envelope wider than 1100 days, so the span is cut to `_MAX_SPAN` before asking. The width is fixed by that limit alone -- parameter count and row volume do not enter into it, which is what the earlier row-budget sizing wrongly assumed.

    THE WINDOWS ARE FETCHED CONCURRENTLY, and this is the only place in the module that runs anything in parallel. The fetch is throughput-bound rather than round-trip-bound -- batching 28 calls down to 14 saved 5% because the cost is moving ~618k rows, not the handshakes -- so the only remaining lever is overlapping the transfers. Windows are independent by construction: disjoint time ranges, deduplicated afterwards, and `_request` returns None instead of raising, so one failed window costs its own data and nothing else. Results are collected in window order, so the concatenation is identical whatever order they land in.
    """
    wins = list(_windows(start, end))
    if not wins:
        return None
    if len(wins) == 1:
        frames = [d] if (d := _request(native_id, pcodes, *wins[0])) is not None else []
    else:
        with ThreadPoolExecutor(max_workers=min(_CONCURRENT, len(wins))) as pool:
            futs = [pool.submit(_request, native_id, pcodes, lo, hi) for lo, hi in wins]
            frames = [f for f in (fu.result() for fu in futs) if f is not None]
    if not frames:
        return None
    raw = pd.concat(frames, ignore_index=True)

    tcol = "time" if "time" in raw.columns else next((c for c in raw.columns if "time" in c.lower()), None)
    if tcol is None or "parameter_code" not in raw.columns:
        return None
    raw = raw.copy()
    raw[tcol] = pd.to_datetime(raw[tcol], errors="coerce", utc=True)
    raw = raw[raw[tcol].notna()]
    raw = raw.drop_duplicates(subset=["parameter_code", tcol], keep="last")
    raw[tcol] = raw[tcol].dt.floor(_GRID)

    # `max` rather than the old build's `first`: within-bin collisions were resolved by concat order there, which is arbitrary and irreproducible across a re-fetch.
    wide = raw.pivot_table(index=tcol, columns="parameter_code", values="value", aggfunc="max")
    wide.columns = [str(c) for c in wide.columns]
    return base.normalise_obs(wide)


def _active_window(params: pd.DataFrame, pcodes: list[str], start, end):
    """Clip the request window to the period the site's own metadata says these parameters cover.

    The single biggest saving in the fetch. Every parameter carries `begin`/`end`, and they differ by more than a decade within one site -- at 01193050 nitrate starts in 2011, dissolved oxygen in 2017 and both nitrogen codes in 2023. Asking for the full config window requests years the API has already declared empty, and an empty window costs a round trip exactly like a full one.
    """
    sel = params[params.parameter_code.astype(str).isin(pcodes)]
    b = pd.to_datetime(sel.get("begin"), utc=True, errors="coerce").min() if "begin" in sel else pd.NaT
    e = pd.to_datetime(sel.get("end"), utc=True, errors="coerce").max() if "end" in sel else pd.NaT
    lo = max(start, b) if pd.notna(b) else start
    hi = min(end, e) if pd.notna(e) else end
    return lo, hi


def _standard_days(idx) -> int:
    """Distinct STANDARD_TZ calendar days in a UTC index -- the same day definition records.py bins on."""
    return len(set(pd.DatetimeIndex(idx).tz_convert(bio.STANDARD_TZ).date))


def fetch(native_id: str, start=None, end=None, channels: str | None = None) -> pd.DataFrame | None:
    """Nitrate at native resolution plus every other parameter the site reports, daily where possible.

    Returns None when the site has no nitrate in the window. Column names are the pcodes themselves; naming is a modelling concern, and a pcode is unambiguous where a friendly name is not (five different codes all mean "turbidity"). A `_dv` suffix marks the columns that came back as daily means rather than at native resolution.

    CONCURRENCY BELONGS TO THE LIBRARY. Each call asks for a whole window and dataretrieval splits and parallelises it, `_CONCURRENT` sub-requests deep. Nothing here threads, chunks or paces, so that constant is the total load this module puts on the API.

    NOTHING IS DROPPED FOR BEING SLOW. The daily service does not publish every parameter -- at 01193050 six of twelve are absent from it, including discharge, dissolved oxygen and pH -- so whatever it fails to return is pulled natively afterwards. Paying for the second pass is the point: a parameter silently missing from the archive is the exact failure the old build's fixed 8-code list produced.

    Parameters
    ----------
    native_id : str
        NWIS site number, without the `USGS-` prefix.
    start, end : timestamp-like, optional
        Defaults to the config year window.
    channels : str, optional
        `'+'`-joined canonical channels the site DECLARED at discovery. Decides the fetch tier inside this adapter (`registry.fetch_tier`) -- the uniform-signature fix: no caller branches on source, and no metadata round trip is spent to learn what discovery already recorded. None or unknown gets the cheap `dv` tier; a `dv` fetch is restricted to the declared pcodes.

    Returns
    -------
    DataFrame or None
        UTC DatetimeIndex, one column per parameter.
    """
    from ... import registry

    _auth()
    tier = registry.fetch_tier(channels)

    # THE CHEAP TIER NEVER ASKS WHAT THE SITE REPORTS. `site_parameters` is one metadata request per site,
    # which at census scale is ~13,700 round trips to learn something `channels_declared` already recorded
    # in chunk 0. A `dv` site therefore goes straight to the daily service, which also needs no API token.
    if tier == "dv":
        want = _tier_pcodes(channels)
        start = pd.Timestamp(start or f"{config.YEAR_START}-01-01", tz="UTC")
        end = pd.Timestamp(end or f"{config.YEAR_END}-12-31", tz="UTC")
        # The cache is filled in batches by `prefetch_dv`; a site the caller never prefetched falls back to
        # its own request, so this path works standalone and the batching stays an optimisation.
        if native_id in _DV_FETCHED:
            # POP, not get. The cache exists to carry a batch from the request to the loop, and holding
            # every site's frame for a whole shard is what pushed three workers to 3.9 GB. Once a site is
            # processed its frame is dead weight.
            dv = _DV_CACHE.pop(native_id, None)
            if dv is not None:
                dv = dv[[c for c in dv.columns if c in want]] if want else dv
        else:
            dv = _daily_values(native_id, want, start, end)
        return dv.add_suffix("_dv") if dv is not None and len(dv) else None

    params = site_parameters(native_id)
    if params.empty:
        return None
    pcodes = params.parameter_code.astype(str).tolist()
    # Only what the registry collects. The old behaviour took every parameter a site published, which is how
    # 162 distinct columns landed on disk for 14 channels' worth of use.
    keep = set(registry.usgs_pcodes())
    pcodes = [p for p in pcodes if p in keep] or pcodes
    nitrate = [p for p in pcodes if p in NITRATE_PCODES] or pcodes
    others = [p for p in pcodes if p not in nitrate]
    start = pd.Timestamp(start or f"{config.YEAR_START}-01-01", tz="UTC")
    end = pd.Timestamp(end or f"{config.YEAR_END}-12-31", tz="UTC")

    nlo, nhi = _active_window(params, nitrate, start, end)
    out = _native(native_id, nitrate, nlo, nhi) if nlo < nhi else None
    if out is None:
        return None
    dv = _daily_values(native_id, others, start, end)
    served = set()
    if dv is not None and len(dv):
        served = set(dv.columns)
        out = out.join(dv.add_suffix("_dv"), how="outer")

    # ALL the unserved parameters in ONE windowed loop, not one loop each. The time envelope is the only per-request limit, so extra parameters ride along free while extra windows cost a full round trip apiece: at 01389005 five parameters ran seven windows each for 27 calls where one batch needs seven. Requesting a parameter outside its own active period is harmless -- it returns nothing for that window -- and costs nothing, because the window would have been requested anyway.
    missing = [p for p in others if p not in served]
    if missing:
        lo, hi = _active_window(params, missing, start, end)
        nat = _native(native_id, missing, lo, hi) if lo < hi else None
        if nat is not None:
            out = out.join(nat, how="outer")
    return out


_TZ_ABBR = {"AST": "America/Puerto_Rico", "EST": "America/New_York", "EDT": "America/New_York",
            "CST": "America/Chicago", "CDT": "America/Chicago", "MST": "America/Denver",
            "MDT": "America/Denver", "PST": "America/Los_Angeles", "PDT": "America/Los_Angeles",
            "AKST": "America/Anchorage", "HST": "Pacific/Honolulu"}


def _tz_from_usgs(abbr, uses_dst) -> pd.Series:
    """The station's CIVIL zone, always the DST-observing one.

    `uses_daylight_savings` is deliberately ignored. It describes the station's RECORDING convention -- many gauges keep timestamps in standard time year-round to avoid the spring discontinuity -- and 43 of the sites here are marked N, including Illinois and New York gauges whose states plainly observe DST. Since the API already returns UTC, that convention is resolved upstream and is irrelevant here.

    What binning needs is the civil local day: what a person in Illinois means by "June 1st". Honouring the recording flag would put those 43 sites on a fixed offset and shift their day boundaries by an hour for half of every year.
    """
    return pd.Series(abbr).astype("string").str.strip().str.upper().map(_TZ_ABBR)


_STATE_ABBR = {
    "Alabama": "al", "Arizona": "az", "Arkansas": "ar", "California": "ca", "Colorado": "co",
    "Connecticut": "ct", "Delaware": "de", "District of Columbia": "dc", "Florida": "fl",
    "Georgia": "ga", "Idaho": "id", "Illinois": "il", "Indiana": "in", "Iowa": "ia",
    "Kansas": "ks", "Kentucky": "ky", "Louisiana": "la", "Maine": "me", "Maryland": "md",
    "Massachusetts": "ma", "Michigan": "mi", "Minnesota": "mn", "Mississippi": "ms",
    "Missouri": "mo", "Montana": "mt", "Nebraska": "ne", "Nevada": "nv", "New Hampshire": "nh",
    "New Jersey": "nj", "New Mexico": "nm", "New York": "ny", "North Carolina": "nc",
    "North Dakota": "nd", "Ohio": "oh", "Oklahoma": "ok", "Oregon": "or", "Pennsylvania": "pa",
    "Rhode Island": "ri", "South Carolina": "sc", "South Dakota": "sd", "Tennessee": "tn",
    "Texas": "tx", "Utah": "ut", "Vermont": "vt", "Virginia": "va", "Washington": "wa",
    "West Virginia": "wv", "Wisconsin": "wi", "Wyoming": "wy",
}
