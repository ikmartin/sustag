"""COMID-keyed attribute tables: StreamCat, Wieczorek, NID, NWM.

THESE NEED NO AGGREGATION. They already arrive keyed on COMID, which is the whole payoff of dropping `global_node_id` -- the join is direct, and nothing has to be rasterised, weighted or re-gridded to use them.

WAITS ON THE NETWORK. Every table here is subset to the COMID set the flowline layer defines, so branch B has to finish first. Pulling nationally and filtering later would be simpler and roughly ten times the download.

WIECZOREK IS STORED AS COLUMN PARTS, one directory per theme-form holding `part_NNN.parquet` plus a `_complete.json` stamp; read it with `read_wieczorek`. Land Cover alone is 1,108 attributes over 1.5M COMIDs, 6.6 GB as a single float32 frame on a 17 GB machine -- building it in one piece is what the OS killed. StreamCat and NID stay single files.

TWO TRAPS CARRIED FORWARD. StreamCat's `Ws` columns are raw upstream SUMS, so they encode basin size unless normalised by `CumAreaKm2` -- a model handed them unnormalised learns to predict area. And several columns are fitted ON the target: StreamCat `sw_flux`, Wieczorek `TNLOAD`/`TNLOSS`, SPARROW `flux_*`. Those are collected only when explicitly asked for, and flagged, because 20 sites sit within 100 m of a SPARROW calibration station and one sits at 0 m.
"""

from __future__ import annotations

import collections
import pathlib
import re
import time

import pandas as pd

from .. import config

# Fitted on nitrogen load. Excluded by default; a caller wanting them must ask, and the column is flagged.
FITTED_ON_TARGET = ("sw_flux", "TNLOAD", "TNLOSS")

# EXACT `themeLabel` values from the catalogue, which is what the filter matches on -- "BMP" and "Land cover" do not exist and silently select nothing. Counts at time of writing: Land Cover 3303, Chemical 1323, Geology 573, Hydrologic Modifications 144, Soils 108, Topographic 88, Best Management Practices 45 = 5,584 attributes.
#
# CLIMATE WATER BALANCE IS EXCLUDED, and it is 7,668 of the release's 14,139 attributes -- more than half the catalogue and most of its size. gridMET covers the same ground at 4 km daily resolution, which is finer than these per-COMID summaries, so keeping both is paying twice for the worse copy.
WIECZOREK_THEMES = ("Soils", "Best Management Practices", "Hydrologic Modifications",
                    "Chemical", "Land Cover", "Geology", "Topographic")

# The NODATA sentinel these tables write where a value is unknown. See `_mask_nodata` for why it cannot be left as a number.
NODATA_SENTINEL = -9999

# Attributes dropped from the pull, with the reason each is dropped. Two kinds, and the distinction matters if this is ever revisited:
#
#   SUPERSEDED -- a better measurement of the SAME quantity is already in the pull. NWALT 1974/1982/1992/2002 are five-vintage 18-class snapshots against annual 123-class CDL (NWALT12 is kept); NLCD imperviousness 2001/2006 against IMPV11, which is in window. Canopy has only a 2011 vintage, so nothing to drop there.
#
#   OUT OF SCOPE -- SOHL's 1940-2005 backcast is a legacy-nitrogen series, and legacy nitrogen is out of scope: the nitrogen surplus record it would pair with is being cut to 2000-2017, so the backcast has no partner and nothing downstream can use it. This is a SCOPE decision, not a quality one -- restoring it means deleting one line here and rebuilding the surplus block back to 1930.
#
# The NLCD land-cover CLASS attributes (NLCD01/04/06/08/11/13/16/19, 16 classes each) are deliberately left alone: unlike imperviousness they are a class breakdown CDL does not reproduce, and 2008 onward is in window regardless.
WIECZOREK_DROP = (
    (r"^(?:CAT|TOT|ACC)_NWALT(?:74|82|92|02)_", "superseded by NWALT12 + annual CDL"),
    (r"^(?:CAT|TOT|ACC)_IMPV(?:01|06)(?:_|$)", "superseded by IMPV11"),
    (r"^(?:CAT|TOT|ACC)_SOHL\d\d_",             "1940-2005 backcast; legacy nitrogen out of scope"),
)

# Every attribute exists in THREE forms and each S3 file holds exactly one, so a request must not mix prefixes. `local` (CAT_) is the reach's own catchment and `totRoute_name` (TOT_) the accumulated upstream watershed -- the same local/accumulated pair as StreamCat's `cat`/`ws`. `divRoute_name` (ACC_) routes through divergences and is dropped: it differs from TOT_ only where flow splits, and nothing downstream asks that question yet.
WIECZOREK_TYPES = {"local": "CAT", "totRoute_name": "TOT"}

# Above this many COMIDs, a pyarrow `isin` pushdown costs more than it saves -- see `wieczorek`.
_PUSHDOWN_MAX = 100_000

# Rows buffered before StreamCat writes a row group. At ~320 columns this is roughly 250 MB resident, against 4 GB for the whole table.
_FLUSH_ROWS = 100_000

# EPA fronts StreamCat with api.data.gov's umbrella, which caps requests per hourly window and says so in the response: `X-Ratelimit-Limit: 1000`. Exceeding it returns an error with an EMPTY body, which is why the failure read as a mystery rather than as a quota.
#
# THE BATCH SIZE IS A QUOTA DECISION, NOT A TUNING ONE. 1,478,684 askable COMIDs at the old batch of 500 needs 2,958 requests against a budget of 1,000 -- that pull could not have completed on any run, with any retry policy, and the log showing it die around request 471 is exactly that. Cost per request is near flat up to 5,000 (2,000 in 33.1s, 5,000 in 31.3s, 10,000 in 68.8s), so a larger batch buys the quota back for free: 296 requests, ~2.6 h.
_STREAMCAT_BATCH = 5_000
_QUOTA_FLOOR = 5            # remaining requests below which we wait for the window rather than spend them
_WINDOW_S = 3660.0          # one hourly window plus a minute of margin

# Retries are for genuinely transient failures only. Against a QUOTA a short backoff is worse than useless -- it spends the scarce resource faster to fail sooner -- so an exhausted quota is detected and waited out instead.
_RETRIES = 3
_BACKOFF_S = 5.0


def latest_vintage(valid_names, include_fitted: bool = False) -> list[str]:
    """One metric per family, taking the most recent vintage where a family has several.

    StreamCat publishes 1,296 names but that is 168 metrics EXPANDED across years: `pctcrop2001 ... pctcrop2019`, `n_ags_1987 ... n_ags_2017`, thirty-one vintages of a single fertiliser series. Requesting all of them against both `cat` and `ws` gives 2,371 columns, measured at 17.8 hours and 28 GB over the AOE's COMIDs -- for a table that is mostly the same quantity at different dates.

    The catalogue in `metrics_df` is the metric set, but its names (`NABD_Dens`, `Precip_Minus_EVT`) are not accepted as input -- the API validates against the lowercased, expanded list. So the family is recovered by stripping a trailing year and keeping the newest member.

    Historical vintages are not gone, only unasked-for. Legacy-nitrogen accumulation needs the series and can request a family explicitly; that is a modelling decision, and it does not belong in a bulk pull of everything.
    """
    import collections
    import re

    fam = collections.defaultdict(list)
    for n in valid_names:
        if "[" in n:
            continue                            # unexpanded template, not a real metric
        if not include_fitted and any(f.lower() in n.lower() for f in FITTED_ON_TARGET):
            continue
        m = re.match(r"^(.*?)_?((?:19|20)\d{2})$", n)
        fam[m.group(1) if m else n].append((int(m.group(2)) if m else -1, n))
    return sorted(max(v)[1] for v in fam.values())


def quota_remaining() -> int | None:
    """Requests left in the current api.data.gov window, or None if the service will not say.

    EPA fronts StreamCat with api.data.gov's umbrella, which returns `X-Ratelimit-Limit` and `X-Ratelimit-Remaining` on EVERY response -- so the allowance is readable rather than something to infer from a failure pattern. Costs one request of the very budget it reports, which is why it is only consulted after a failure and not before each batch.
    """
    import requests

    try:
        r = requests.post("https://api.epa.gov/StreamCat/streams/metrics",
                          data={"name": "al2o3", "aoi": "cat", "comid": "6621656"}, timeout=30)
        v = r.headers.get("X-Ratelimit-Remaining")
        return int(v) if v is not None else None
    except Exception:
        return None


def _patch_streamcat_years() -> None:
    """Survive `pynhd`'s StreamCat year parse, which EPA's catalogue now breaks.

    `StreamCat.__init__` ends by building `valid_years` from the catalogue's YEAR column, understanding `1996,2001` and `2001-2019` but not the `1990:2017` colon range EPA has since started publishing -- so it raises `ValueError: invalid literal for int() with base 10: '1990:2017'`. That kills `pynhd.streamcat()` too, which constructs a `StreamCatValidator` internally, so there is no way to fetch without going through it.

    The raise is the LAST statement of `__init__`: `valid_names`, `valid_regions`, `valid_aois` and `metrics_df` are all assigned before it. Catching it and leaving `valid_years` empty therefore yields a fully usable object, and we never read `valid_years` -- vintages are collapsed by `latest_vintage` off the names instead. Narrowed to this exact message so any other failure still surfaces.
    """
    from pynhd import nhdplus_derived as nd

    if getattr(nd.StreamCat, "_sustag_year_patch", False):
        return
    orig = nd.StreamCat.__init__

    def __init__(self, lakes_only: bool = False) -> None:
        try:
            orig(self, lakes_only)
        except ValueError as e:
            if "invalid literal for int()" not in str(e):
                raise
            self.valid_years = {}

    nd.StreamCat.__init__ = __init__
    nd.StreamCat._sustag_year_patch = True


def streamcat_names() -> list[str]:
    """Every metric name the StreamCat API accepts.

    NOT VIA `StreamCat()`. Its constructor also builds a `valid_years` map, and that parse understands `1996,2001` and `2001-2019` but not the `1990:2017` colon range EPA has since published -- so it dies with `ValueError: invalid literal for int() with base 10: '1990:2017'` before returning, taking the name list down with a field we never read. The names come from the same endpoint the constructor uses, one step earlier.

    Falls back to the class if the endpoint shape ever changes, so the direct read is an optimisation over the library rather than a fork of it.
    """
    import async_retriever as ar

    try:
        resp = ar.retrieve_json(["https://api.epa.gov/StreamCat/streams/metrics"])
        return [d["name"] for d in resp[0]["items"][0]["name_options"]]
    except Exception as e:
        print(f"    streamcat catalogue: direct read failed ({type(e).__name__}), trying pynhd")
        from pynhd import StreamCat

        return list(StreamCat().valid_names)


def _path(name: str):
    return config.ACQ_ATTRIBUTES / f"{name}.parquet"


def _covered(name: str, comids, key: str = "comid") -> bool:
    """True when the artifact on disk already covers this COMID set AND can be joined to it.

    EXISTENCE IS NOT COVERAGE. A skip-if-the-file-exists check treats a 2,000-COMID probe as a finished 1.5M-COMID pull, reports "on disk", and the run completes green on data that is 0.1% of what was asked for. Nothing downstream can tell the difference -- the columns are all present and the rows are all real.

    NOR IS A ROW COUNT. Eight Wieczorek files were written with no COMID at all and every one passed this check, because they held the 2.7M NATIONAL rows and the threshold was set against the AOE's 1.5M -- a file with too MANY rows is a file that was never filtered, which is the opposite of covered. The key column is therefore checked first: without it the artifact cannot be joined to anything and its row count means nothing.
    """
    import pyarrow.parquet as pq

    p = _path(name)
    if not p.exists():
        return False
    names = [n.lower() for n in pq.read_schema(p).names]
    if key.lower() not in names:
        print(f"    {name}: on disk but has no {key} column -- unjoinable, refetching")
        return False
    have = pq.read_metadata(p).num_rows
    if have >= 0.99 * len(comids):
        return True
    print(f"    {name}: on disk but only {have:,} of {len(comids):,} COMIDs -- refetching")
    return False


def _write(df: pd.DataFrame, name: str) -> int:
    config.ACQ_ATTRIBUTES.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    out["aoe_stamp"] = config.aoe_stamp()
    p = _path(name)
    tmp = p.with_name(p.name + ".tmp")
    out.to_parquet(tmp, index=False)
    tmp.replace(p)
    return len(out)


def comid_set() -> list[int]:
    """Every COMID in the AOE, from the flowline layer branch B wrote."""
    import geopandas as gpd

    from . import network

    if not network.exists():
        raise FileNotFoundError("network layers are missing -- run branch B (network) first")
    fl = gpd.read_parquet(network._path("flowlines"))
    c = pd.to_numeric(fl.get("comid", fl.get("COMID")), errors="coerce").dropna().astype("int64")
    return sorted(set(c))


def catchment_comids() -> set[int]:
    """COMIDs in the AOE that OWN a catchment -- the set StreamCat can actually answer for.

    StreamCat summarises a catchment, so a reach without one has nothing to summarise and is simply absent from the response. Measured exactly: of a 2,000-COMID probe, the 27 StreamCat withheld were precisely the 27 with no catchment, and every COMID owning one came back. Asking for them individually returns zero rows, so it is the data and not the batching.

    This matters twice. It removes 32,411 pointless requests (2.14% of the AOE), and it makes the coverage check exact -- comparing a StreamCat pull against the FLOWLINE count can never reach 99% and would refetch a complete table on every run, forever.
    """
    import pyarrow.parquet as pq

    from . import network

    col = next(c for c in pq.read_schema(network._path("catchments")).names
               if c.lower() in ("featureid", "comid"))
    ids = pq.read_table(network._path("catchments"), columns=[col]).column(col).to_pandas()
    return set(pd.to_numeric(ids, errors="coerce").dropna().astype("int64"))


def streamcat(comids: list[int], force: bool = False, include_fitted: bool = False,
              batch: int = _STREAMCAT_BATCH) -> int:
    """EPA StreamCat, in both local (`cat`) and accumulated (`ws`) form, minus the fitted-on-target metrics.

    The accessor is the MODULE-LEVEL `pynhd.streamcat`, not a method on the class. `StreamCat` itself only exposes catalogue attributes (`valid_names`, `metrics_df`, `valid_aois`) and `all_metrics_bycomid`, which takes a single COMID.

    BOTH AOIs ARE FETCHED. `cat` is the reach's own catchment and `ws` the accumulated upstream watershed -- the same local/accumulated pair the pipeline now keys everything on. `ws` values are raw upstream SUMS, so anything using them must normalise by drainage area or the column encodes basin size; that normalisation belongs at feature time, not here, so the raw values are stored as published.

    Names carrying `[Year]` or `[Slope]` are skipped: they are templates rather than metrics, and expanding them is a separate decision about which vintages to keep.

    Written INCREMENTALLY, one row group per `_FLUSH_ROWS`, so peak memory is a buffer rather than the whole table.
    """
    have_cat = catchment_comids()
    askable = [c for c in comids if c in have_cat]
    if _covered("streamcat", askable, key="COMID") and not force:
        print("  streamcat: on disk"); return 0
    _patch_streamcat_years()
    from pynhd import streamcat as sc_get

    names = latest_vintage(streamcat_names(), include_fitted)
    n_req = -(-len(askable) // batch)
    rem = quota_remaining()
    print(f"    {len(names)} metrics x {len(askable):,} COMIDs with a catchment "
          f"({len(comids) - len(askable):,} reaches skipped, no catchment to summarise), "
          f"in {n_req} request(s) of {batch:,}"
          + (f"; quota {rem} left this window" if rem is not None else ""), flush=True)
    # SAY IT BEFORE SPENDING TWO HOURS, not after. A pull needing more requests than the window allows cannot finish, and the old batch of 500 needed 2,958 against 1,000 -- which presented as a run that died two thirds of the way in for no stated reason.
    if rem is not None and n_req > rem:
        print(f"    WARNING: {n_req} requests needed but only {rem} left in this window; "
              f"the run will pause {_WINDOW_S / 60:.0f} min when it runs out", flush=True)
    comids = askable

    import pyarrow as pa
    import pyarrow.parquet as pq

    # FLUSHED TO DISK, NOT ACCUMULATED. Collecting 3,000 batch frames and concatenating at the end holds the finished table AND the list it came from at the same moment -- about 320 columns over 1.5M COMIDs, so roughly 4 GB doubled to 8 at the concat, on the 17 GB machine that has already been OOM-killed once this build. Row groups append, so the peak is one buffer.
    p = _path("streamcat")
    tmp = p.with_name(p.name + ".tmp")
    writer, cols, buf, held, written = None, None, [], 0, 0

    def flush():
        nonlocal writer, cols, buf, held, written
        if not buf:
            return
        df = pd.concat(buf, ignore_index=True)
        buf, held = [], 0
        if cols is None:
            cols = list(df.columns)
        elif list(df.columns) != cols:
            # A batch may come back with a metric the previous ones lacked. Align on the first batch's schema or ParquetWriter refuses the row group; a genuinely new column is reported rather than silently dropped.
            extra = [c for c in df.columns if c not in cols]
            if extra:
                print(f"    dropping {len(extra)} column(s) absent from the first batch: {extra[:4]}")
            df = df.reindex(columns=cols)
        # DTYPES ARE PINNED, NOT INFERRED. A metric that happens to be whole-numbered across the first 100k rows infers as int64, and the first float in a later batch then fails the row group with "Float value 0.238300 was truncated converting to int64". Every StreamCat metric is a measurement, so float64 is the honest type for all of them and it cannot drift between batches. Cast in ONE call -- 296 separate assignments fragment the frame and pandas warns about it once per flush.
        df = df.astype({c: "float64" for c in df.columns if c != "comid"} | {"comid": "int64"})
        # The stamp is appended in ARROW, not pandas: `df["aoe_stamp"] = ...` inserts into a frame concatenated from thousands of small batches, which pandas flags as highly fragmented once per flush.
        tbl = pa.Table.from_pandas(df, preserve_index=False).append_column(
            "aoe_stamp", pa.array([config.aoe_stamp()] * len(df), pa.string()))
        if writer is None:
            writer = pq.ParquetWriter(tmp, tbl.schema)
        writer.write_table(tbl)
        written += len(df)

    def fetch(chunk: list[int]) -> bool:
        """One batch. Waits out an exhausted quota rather than spending retries against it. True if it landed."""
        nonlocal held
        last = "no attempt"
        for attempt in range(_RETRIES):
            try:
                got = sc_get(metric_names=names, metric_areas=["cat", "ws"], comids=chunk)
                buf.append(got)
                held += len(got)
                return True
            except Exception as e:
                last = f"{type(e).__name__}: {str(e)[:60]}"
                if attempt == _RETRIES - 1:
                    break
                # ASK THE SERVICE WHY. api.data.gov reports the remaining allowance on every response, so an exhausted quota is a fact to read rather than a pattern to infer -- which is the whole reason this failure looked like a mystery the first time.
                rem = quota_remaining()
                if rem is not None and rem <= _QUOTA_FLOOR:
                    print(f"    quota exhausted ({rem} left) -- waiting {_WINDOW_S / 60:.0f} min "
                          f"for the window to reset", flush=True)
                    time.sleep(_WINDOW_S)
                else:
                    time.sleep(_BACKOFF_S * (2 ** attempt))
        print(f"    batch of {len(chunk)} failed after {_RETRIES} tries -- {last}", flush=True)
        return False

    failed: list[int] = []
    for i in range(0, len(comids), batch):
        if not fetch(comids[i:i + batch]):
            failed += comids[i:i + batch]
        if held >= _FLUSH_ROWS:
            flush()
        if (i // batch + 1) % 20 == 0:
            print(f"    {min(i + batch, len(comids)):,}/{len(comids):,} "
                  f"({written:,} written{f', {len(failed):,} to retry' if failed else ''})",
                  flush=True)

    # SECOND PASS over what the service refused, at the SAME batch size. Splitting into smaller batches would be the natural instinct and is exactly wrong here: the constraint is a REQUEST count, so subdividing spends more of the scarce thing to ask for the same data. `fetch` already waits out an exhausted window, so this sweep exists only to catch batches that failed while the quota was recovering. Dropping them instead is how a run finishes green with a hole in it.
    if failed:
        print(f"    retrying {len(failed):,} COMID(s) the service refused", flush=True)
        still: list[int] = []
        for i in range(0, len(failed), batch):
            if not fetch(failed[i:i + batch]):
                still += failed[i:i + batch]
            if held >= _FLUSH_ROWS:
                flush()
        failed = still
    flush()
    if writer is None:
        return 0
    writer.close()

    # A SHORTFALL IS AN ERROR, NOT A FOOTNOTE. `askable` is exactly the set StreamCat can answer for, so anything missing was refused rather than absent. Writing the partial file anyway would leave an artifact that either wastes 2.6 hours refetching or, below the coverage threshold, passes as complete.
    if failed:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"streamcat: {len(failed):,} of {len(comids):,} COMIDs never returned after retries "
            f"({len(failed) / len(comids):.2%}); partial file discarded. Rerun to resume -- the "
            f"service throttles a sustained pull and usually recovers.")
    tmp.replace(p)
    return written


def _mark_complete(d: pathlib.Path, names: list[str], rows: int, dead: list[str]) -> None:
    """Stamp a theme-form directory as finished. Written LAST, so an interrupted pull is not mistaken for a complete one.

    `nodata_masked` is a FORMAT VERSION, not a statistic. Parts written before the sentinel mask hold -9999 as a live number and are wrong in a way no row or column count reveals, so `_dir_covered` requires the flag and anything older refetches itself.
    """
    import json

    (d / "_complete.json").write_text(json.dumps(
        {"n_attrs": len(names), "n_rows": rows, "aoe_stamp": config.aoe_stamp(),
         "nodata_masked": True, "dropped_all_nodata": sorted(dead),
         "attrs": sorted(n for n in names if n not in set(dead))}))


def _dir_covered(slug: str, n_attrs: int, comids) -> bool:
    """True when a part-file directory holds a finished pull of this attribute set against this AOE."""
    import json

    d = config.ACQ_ATTRIBUTES / slug
    f = d / "_complete.json"
    if not f.exists():
        return False
    try:
        m = json.loads(f.read_text())
    except Exception:
        return False
    if not m.get("nodata_masked"):
        print(f"    {slug}: on disk but written before the NODATA mask -- refetching"); return False
    if m.get("aoe_stamp") != config.aoe_stamp():
        print(f"    {slug}: on disk but cut against a different AOE -- refetching"); return False
    if m.get("n_attrs") != n_attrs:
        print(f"    {slug}: on disk with {m.get('n_attrs')} attrs, catalogue now has {n_attrs} "
              f"-- refetching"); return False
    if m.get("n_rows", 0) < 0.99 * len(comids):
        print(f"    {slug}: on disk but only {m.get('n_rows'):,} of {len(comids):,} COMIDs "
              f"-- refetching"); return False
    return True


def read_wieczorek(slug: str, columns: list[str] | None = None) -> pd.DataFrame:
    """One theme-form, reassembled from its parts and keyed on `comid`.

    A theme-form is stored as column PARTS because Land Cover is 1,108 attributes over 1.5M COMIDs -- 6.6 GB as one float32 frame, on a 17 GB machine that already died once building it. Parts make the write bounded and make a narrow read cheap: asking for four columns touches the one part that holds them instead of opening the whole theme.
    """
    d = config.ACQ_ATTRIBUTES / slug
    parts = sorted(d.glob("part_*.parquet"))
    if not parts:
        raise FileNotFoundError(f"{d} has no parts -- run chunk 1 branch D")
    out = None
    for p in parts:
        import pyarrow.parquet as pq

        have = [n for n in pq.read_schema(p).names if n != "comid"]
        want = have if columns is None else [c for c in have if c in set(columns)]
        if not want:
            continue
        df = pd.read_parquet(p, columns=["comid"] + want)
        out = df if out is None else out.merge(df, on="comid", how="outer")
    return out if out is not None else pd.DataFrame({"comid": pd.Series(dtype="int64")})


def _with_comid(df: pd.DataFrame) -> pd.DataFrame:
    """Guarantee a real `comid` COLUMN. Raises if the frame has no recoverable key.

    `nhdplus_attrs_s3` returns COMID as the INDEX, not a column. Searching `df.columns` for it therefore finds nothing, which silently cost two things at once: the AOE filter matched no key and kept all 2,699,864 national rows, and `to_parquet(index=False)` then dropped the index, so the artifact had no join key at all. Eight theme-forms, 3 GB, unjoinable -- and every one of them passed a row-count coverage check, because the national row count clears any threshold set against the AOE's.

    Raising is deliberate: a Wieczorek table without a COMID is worthless, so it must not be written.
    """
    out = df.reset_index() if df.index.name else df.copy()
    key = next((c for c in out.columns if c.upper() == "COMID"), None)
    if key is None:
        raise KeyError(f"no COMID in index or columns; got index={df.index.name!r} "
                       f"columns={list(out.columns)[:6]}")
    return out.rename(columns={key: "comid"})


def _mask_nodata(part: pd.DataFrame) -> tuple[int, list[str]]:
    """`NODATA_SENTINEL` -> NaN in place. Returns (cells masked, columns that were entirely NODATA).

    THE SENTINEL IS A NUMBER UNTIL SOMETHING MAKES IT MISSING. These attributes are percentages and rates -- `CAT_CDL11_1` is percent of catchment in corn, real values 0 to 100 -- and where the publisher had no answer it wrote -9999 rather than leaving the cell blank. Stored literally, a catchment with unknown corn cover reads as MINUS NINE THOUSAND PERCENT corn, and a tree splits on it happily. Because missing coverage is geographically clustered, "below -5000" is really "which region is this", which is a basin identifier wearing a covariate's name. NaN is the representation that means what we intend, and XGBoost handles it natively by learning a default direction per split.

    `pynhd`'s own `nodata=` argument does NOT do this -- measured, the sentinels survive it either way. Every one of the 1,146 attributes whose description declares a sentinel declares -9999 and nothing else, so a blanket rule is safe here. It is NOT safe in general: `CAT_TILES_Early90s` encodes NODATA as 100, which no description states.

    Rate is low -- 0.21% of cells, about 318 rows in 1.5M for a typical column -- but the screen it feeds has a REG noise floor of 0.0085, and a spurious region-identifying split is exactly how a fake winner gets manufactured.
    """
    cols = [c for c in part.columns if c != "comid"]
    hit = part[cols] == NODATA_SENTINEL
    n = int(hit.to_numpy().sum())
    if n:
        part[cols] = part[cols].mask(hit)
    empty = [c for c in cols if part[c].isna().all()]
    return n, empty


def wieczorek(comids: list[int], force: bool = False, batch: int = 100) -> int:
    """The ScienceBase attribute release, via S3, one theme-form at a time.

    `pynhd.nhdplus_attrs_s3()` rather than ScienceBase directly: ScienceBase times out on a catalogue this size, S3 answers the metadata in about a tenth of a second.

    THE SIGNATURE IS `(attr_names, pyarrow_filter, nodata)` -- it takes attribute NAMES, not theme names, and has no `comids` argument. Passing a theme string selects nothing and passing `comids=` is a TypeError.

    READ IN COLUMN BATCHES. Land Cover carries 1,108 attributes against 2.7M national rows -- 3 billion float64s, about 24 GB, which the OS kills rather than swaps. Each batch is filtered to the AOE before the next is read, so peak memory is one batch of the national table plus the AOE-sized accumulation, and the columns are concatenated at the end.

    Written per theme-form rather than joined into one frame. A 5,584-column outer join across seven themes is a large in-memory object for no benefit, and a failed theme then costs only itself -- which matters because these calls have failed before, and a loop that prints and continues can otherwise make a partial result look complete.
    """
    from pynhd import nhdplus_attrs_s3

    meta = nhdplus_attrs_s3()
    keep_ids = set(comids)
    want, got, missing = 0, 0, []
    for theme in WIECZOREK_THEMES:
        for wtype, prefix in WIECZOREK_TYPES.items():
            want += 1
            slug = f"{theme.lower().replace(' ', '_')}_{prefix.lower()}"
            allnames = meta[(meta.themeLabel == theme)
                            & (meta.watershedType == wtype)].ID.tolist()
            names, why = [], collections.Counter()
            for n in allnames:
                if any(f.lower() in n.lower() for f in FITTED_ON_TARGET):
                    why["fitted on target"] += 1; continue
                hit = next((r for p, r in WIECZOREK_DROP if re.match(p, n)), None)
                if hit:
                    why[hit] += 1; continue
                names.append(n)
            if why:
                print(f"    {theme}/{prefix}: {sum(why.values())} attr(s) not requested -- "
                      + "; ".join(f"{v} {k}" for k, v in why.most_common()))
            if not names:
                print(f"    {theme}/{prefix}: nothing in the catalogue")
                missing.append(slug); continue
            if _dir_covered(f"wieczorek_{slug}", len(names), comids) and not force:
                print(f"    {theme}/{prefix}: on disk"); got += 1; continue
            try:
                d = config.ACQ_ATTRIBUTES / f"wieczorek_{slug}"
                d.mkdir(parents=True, exist_ok=True)
                rows, masked, dead = 0, 0, []
                for i in range(0, len(names), batch):
                    # The names must all share one prefix -- each S3 file holds a single form, so mixing CAT_ and ACC_ fails with `No match for FieldRef.Name(ACC_HGA)`.
                    part = _with_comid(nhdplus_attrs_s3(attr_names=names[i:i + batch]))
                    part = part[part.comid.isin(keep_ids)]
                    n_mask, empty = _mask_nodata(part)
                    masked += n_mask
                    # A column that is NODATA for every COMID in the AOE carries nothing. Dropped rather than stored as 1.5M NaNs, and named in the stamp so the loss is auditable rather than silent.
                    if empty:
                        dead += empty
                        part = part.drop(columns=empty)
                    for c in part.columns:
                        if c != "comid" and part[c].dtype == "float64":
                            part[c] = part[c].astype("float32")
                    rows = len(part)
                    p = d / f"part_{i // batch:03d}.parquet"
                    tmp = p.with_name(p.name + ".tmp")
                    part.to_parquet(tmp, index=False)
                    tmp.replace(p)
                    del part
                    if len(names) > batch:
                        print(f"      {theme}/{prefix}: {min(i + batch, len(names))}/{len(names)} "
                              f"attrs, {rows:,} rows", flush=True)
                _mark_complete(d, names, rows, dead)
                print(f"    {theme}/{prefix}: {len(names) - len(dead)} attrs -> {rows:,} rows "
                      f"in {(len(names) - 1) // batch + 1} part(s); "
                      f"{masked:,} NODATA cell(s) masked", flush=True)
                if dead:
                    print(f"      dropped {len(dead)} all-NODATA column(s): {sorted(dead)[:6]}"
                          + (" ..." if len(dead) > 6 else ""), flush=True)
                got += 1
            except Exception as e:
                print(f"    {theme}/{prefix}: {type(e).__name__}: {str(e)[:80]}")
                missing.append(slug)
    # Count what RETURNED against what was REQUESTED; a per-theme try/except otherwise reports success for a partial pull.
    print(f"  wieczorek: {got}/{want} theme-forms" + (f"  MISSING {missing}" if missing else ""))
    return got


def nid(force: bool = False) -> int:
    """National Inventory of Dams within the AOE, for upstream reservoir influence.

    WHY DAMS ARE A NITRATE COVARIATE. Water slowed in an impoundment denitrifies, so a sensor with storage upstream reads lower than its land use alone predicts. Storage volume and drainage area are what the removal terms are built from.

    NID IS IN `pygeohydro`, NOT `pynhd`, and it has no `get_all()` -- the accessor is `get_bygeom(geometry, geo_crs)`. Asking for the AOE directly beats pulling the national inventory and filtering, and it keeps the artifact consistent with every other table here, which is cut to the same extent.

    Kept non-fatal: dams are a chunk-3 covariate and nothing in chunks 1 or 2 reads them, so a failure here reports and returns rather than taking StreamCat and Wieczorek down with it.
    """
    import geopandas as gpd

    if _path("nid").exists() and not force:
        print("  nid: on disk"); return 0

    cache = config.ACQ_ATTRIBUTES / "nid_national.gpkg"
    if not cache.exists() or cache.stat().st_size < 1_000_000:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.unlink(missing_ok=True)
        try:
            _download_nid(cache)
        except Exception as e:
            print(f"  nid: download failed -- {type(e).__name__}: {str(e)[:80]}")
            return 0
        if cache.stat().st_size < 1_000_000:
            note = cache.read_text(errors="replace").strip()[:90]
            cache.unlink(missing_ok=True)
            print(f"  nid: the Army Corps export is not ready -- '{note}'. Retry shortly.")
            return 0

    dams = gpd.read_file(cache, engine="pyogrio")
    hit = dams.iloc[list(dams.sindex.query(config.aoe_geometry(), predicate="intersects"))]
    df = pd.DataFrame(hit.drop(columns="geometry", errors="ignore"))
    # The inventory mixes types within columns; parquet will not take object dtype, and losing the table to a serialisation error would be a poor trade for a covariate.
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].astype("string")
    print(f"    {len(dams):,} dams nationally -> {len(df):,} in the AOE")
    return _write(df, "nid")


def _download_nid(dest: pathlib.Path) -> None:
    """Fetch the national geopackage straight from the Army Corps.

    NOT `pygeohydro.NID`, which reads the same file and then casts it to a schema the Corps no longer publishes -- `KeyError: 'hazardId' not found` on 92,604 dams and 98 columns that geopandas opens without complaint. The endpoint also BUILDS the export on demand and answers with 86 bytes of plain text while none is staged, which the wrapper writes to `.gpkg` and reports as an unrecognised file format; the size check above catches that and says what it means.
    """
    import shutil
    import urllib.request

    url = "https://nid.sec.usace.army.mil/api/nation/gpkg"
    req = urllib.request.Request(url, headers={"User-Agent": "sustag-build2"})
    tmp = dest.with_suffix(".gpkg.tmp")
    with urllib.request.urlopen(req, timeout=600) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    tmp.replace(dest)


def nwm(comids: list[int], force: bool = False) -> int:
    """NWM v3.0 retrospective, daily-aggregated, at the site COMIDs first.

    Site COMIDs occupy 22 of the store's 93 feature chunks, so a narrow pull is about 4.2x cheaper than a full one. Partitioned by COMID so widening later is an append rather than a refetch.
    """
    if _path("nwm").exists() and not force:
        print("  nwm: on disk"); return 0
    raise NotImplementedError(
        "NWM zarr pull not implemented yet -- feature_id = COMID, chunks [672, 30000]; "
        "pull at site COMIDs first and partition the store by COMID")


def main(force: bool = False, include_fitted: bool = False) -> dict:
    comids = comid_set()
    print(f"  {len(comids):,} COMIDs in the AOE")
    out = {}
    for name, fn in (("streamcat", lambda: streamcat(comids, force, include_fitted)),
                     ("wieczorek", lambda: wieczorek(comids, force)),
                     ("nid", lambda: nid(force))):
        try:
            out[name] = fn()
            print(f"  {name}: {out[name]:,} rows" if out[name] else f"  {name}: skipped")
        except Exception as e:
            out[name] = 0
            # Not truncated to 80 -- a coverage shortfall explains itself in the message, and clipping it turns a diagnosable failure into a mystery.
            print(f"  {name}: FAILED {type(e).__name__}: {e}")
    return out
