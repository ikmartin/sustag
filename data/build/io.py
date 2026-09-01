"""Atomic writes, artifact stamps, and the local-calendar-day rule.

WHY ATOMIC. A Ctrl-C mid-write must not leave a truncated artifact the next run treats as complete. Every write goes to a sibling temp file and is renamed, which is atomic within a filesystem.

WHY STAMPS LIVE HERE, IN ONE READER/WRITER PAIR. Every derived artifact records the rule it was derived under, and the reader checks -- the chapter's provenance principle. build2 wrote stamps everywhere and read them in exactly one place (`dams._cached`), which is how a table cut against a different extent could be handed to a caller that never asked for it. Here `write_parquet(stamps=...)` embeds them in the pyarrow schema metadata and `read_parquet(expect=...)` refuses on mismatch, so a consumer cannot forget to check without visibly opting out. Non-parquet artifacts get a `{name}.stamps.json` sidecar via `write_stamps_sidecar`.

WHY A DAY IS CENTRAL STANDARD TIME, EVERYWHERE, ALL YEAR. Resampling a UTC-aware index makes a "daily max" run 18:00-18:00 local Central; per-site civil zones drag in DST, where two days a year are 23 or 25 hours long and neighbouring sites get boundaries an hour apart for no gain. `Etc/GMT+6` is Central Standard year-round: every day in every source is the same 24 hours. An eastern-seaboard day runs 01:00-01:00 local; the benefit is that a day means one thing. Each sensor's civil zone is still RECORDED as `tz` so the choice stays auditable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

# The single binning zone for the whole AOE: Central Standard year-round, no DST.
STANDARD_TZ = "Etc/GMT+6"

# Namespace for this build's stamps inside parquet key-value metadata. One prefix so foreign metadata
# (pandas' own schema blob) is never mistaken for ours.
_STAMP_PREFIX = b"databuild.stamp."

# Every state in or adjacent to the AOE, for when `timezonefinder` is absent. Exact for the AOE core;
# split-zone states are assigned their eastern-portion zone, where our sites in them actually sit.
STATE_TZ = {
    "ia": "America/Chicago", "mn": "America/Chicago", "il": "America/Chicago",
    "mo": "America/Chicago", "wi": "America/Chicago", "ne": "America/Chicago",
    "ks": "America/Chicago", "sd": "America/Chicago", "nd": "America/Chicago",
    "ar": "America/Chicago", "la": "America/Chicago", "ms": "America/Chicago",
    "ok": "America/Chicago", "tx": "America/Chicago", "al": "America/Chicago",
    "tn": "America/Chicago", "ky": "America/New_York", "in": "America/New_York",
    "oh": "America/New_York", "mi": "America/New_York", "pa": "America/New_York",
    "ny": "America/New_York", "va": "America/New_York", "wv": "America/New_York",
    "md": "America/New_York", "de": "America/New_York", "nj": "America/New_York",
    "ct": "America/New_York", "ma": "America/New_York", "ri": "America/New_York",
    "nh": "America/New_York", "vt": "America/New_York", "me": "America/New_York",
    "dc": "America/New_York", "nc": "America/New_York", "sc": "America/New_York",
    "ga": "America/New_York", "fl": "America/New_York",
}
DEFAULT_TZ = "America/Chicago"

try:
    from timezonefinder import TimezoneFinder

    _TF = TimezoneFinder()
except Exception:
    _TF = None


def resolve_tz(lat: float, lon: float, state: str | None = None) -> str:
    """IANA zone for a site: point-in-polygon when available, else the state map, else Central."""
    if _TF is not None:
        try:
            z = _TF.timezone_at(lat=float(lat), lng=float(lon))
            if z:
                return z
        except Exception:
            pass
    return STATE_TZ.get(str(state).strip().lower(), DEFAULT_TZ) if state else DEFAULT_TZ


def local_day(index: pd.DatetimeIndex):
    """Calendar dates of a UTC-aware index under STANDARD_TZ. The one place a day is decided."""
    return index.tz_convert(STANDARD_TZ).date


def gap_stats(dates) -> tuple[int, int]:
    """(n_days, longest_gap_days) over a set of local dates; gap is the longest run with no observation."""
    d = pd.Series(pd.to_datetime(pd.Series(list(dates)), errors="coerce")).dropna().dt.normalize().drop_duplicates().sort_values()
    if d.empty:
        return 0, 0
    if len(d) == 1:
        return 1, 0
    return len(d), int(d.diff().dt.days.max() - 1)


# ---- atomic writes with embedded stamps -----------------------------------------------------------

def write_parquet(df: pd.DataFrame, path: Path, stamps: dict | None = None, index: bool = False) -> Path:
    """Atomic parquet write, stamps embedded in the schema metadata. Returns `path`.

    Stamps are string key-value pairs under the `databuild.stamp.` prefix -- snapshot id, aoe, namespace, schema fingerprint, ledger hashes, whatever the artifact depends on. Embedded rather than columnar so they cost nothing per row and cannot be dropped by a column selection.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(df, preserve_index=index)
    if stamps:
        meta = dict(table.schema.metadata or {})
        for k, v in stamps.items():
            meta[_STAMP_PREFIX + str(k).encode()] = str(v).encode()
        table = table.replace_schema_metadata(meta)
    tmp = path.with_name(path.name + ".tmp")
    pq.write_table(table, tmp)
    os.replace(tmp, path)
    return path


def copy_parquet_stamped(src: Path, dst: Path, stamps: dict | None = None) -> Path:
    """Copy a parquet file row group by row group, attaching stamps. The frame is NEVER materialised.

    `write_parquet(read_parquet(p))` is the obvious way to restamp an artifact and it does not survive a large one: the twelve-component nutrients product is 320,834,088 rows whose `product` column is a string, so pandas needs ~10 GB resident to hold a file that is 3.5 GB on disk and 3.8 GB in arrow. Streaming holds ONE row group -- about 12 MB here -- and writes byte-equivalent output, so publication's cost stops scaling with the product it publishes.

    Row group boundaries are preserved, which also preserves the statistics a predicate-pushdown reader prunes on.
    """
    import pyarrow.parquet as pq

    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    pf = pq.ParquetFile(src)
    schema = pf.schema_arrow
    if stamps:
        meta = dict(schema.metadata or {})
        for k, v in stamps.items():
            meta[_STAMP_PREFIX + str(k).encode()] = str(v).encode()
        schema = schema.with_metadata(meta)
    tmp = dst.with_name(dst.name + ".tmp")
    with pq.ParquetWriter(tmp, schema) as w:
        for i in range(pf.metadata.num_row_groups):
            w.write_table(pf.read_row_group(i).replace_schema_metadata(schema.metadata))
    os.replace(tmp, dst)
    return dst


def input_stamp(*paths) -> str:
    """A short hash over the identity of every input file a product reads: name, size, mtime.

    THE STAMP MUST NAME EVERY INPUT THE FUNCTION READS. A product stamped on the extent alone leaves a cached artifact standing when an input is rebuilt underneath it -- a nineteen-archive reader ignores a twentieth, and a manual acquisition arriving between two runs never joins, with no line printed to say so. `d8._accum_cache_file` is the model: key on the identity of what you read, and a changed input becomes unreachable rather than silently reused.

    Missing files contribute their absence, so acquiring one is itself a change. Pair with `read_parquet(expect=)` for stamped artifacts, or fold into a checkpoint directory name where there is no single artifact to stamp.
    """
    import hashlib

    h = hashlib.sha256()
    for q in sorted(str(x) for x in paths):
        try:
            st = Path(q).stat()
            h.update(f"{q}:{st.st_size}:{st.st_mtime_ns}".encode())
        except FileNotFoundError:
            h.update(f"{q}:absent".encode())
    return h.hexdigest()[:12]


def read_stamps(path: Path) -> dict[str, str]:
    """the build's stamps on a parquet artifact, from the footer alone -- no row is touched."""
    import pyarrow.parquet as pq

    meta = pq.ParquetFile(path).schema_arrow.metadata or {}
    n = len(_STAMP_PREFIX)
    return {k[n:].decode(): v.decode() for k, v in meta.items() if k.startswith(_STAMP_PREFIX)}


def read_parquet(path: Path, expect: dict | None = None, columns=None) -> pd.DataFrame:
    """Read a parquet artifact, refusing when its stamps disagree with `expect`.

    THE READER CHECKS. `expect` maps stamp name -> required value; a missing stamp fails the same way a wrong one does, because an unstamped artifact predates the rule it would have recorded and is exactly the case the check exists for. Pass `expect=None` to read unconditionally -- a visible opt-out, for tools that inspect rather than consume.
    """
    if expect:
        got = read_stamps(path)
        bad = {k: (got.get(k), v) for k, v in expect.items() if got.get(k) != str(v)}
        if bad:
            detail = "; ".join(f"{k}: artifact={a!r} expected={e!r}" for k, (a, e) in bad.items())
            raise StaleArtifactError(f"{Path(path).name} was derived under different rules -- {detail}")
    return pd.read_parquet(path, columns=columns)


class StaleArtifactError(RuntimeError):
    """An artifact's stamps disagree with the rules the caller is building under."""


def write_stamps_sidecar(path: Path, stamps: dict) -> Path:
    """`{name}.stamps.json` beside a non-parquet artifact (PNG, tif, zip product)."""
    side = Path(path).with_name(Path(path).name + ".stamps.json")
    tmp = side.with_name(side.name + ".tmp")
    tmp.write_text(json.dumps({str(k): str(v) for k, v in stamps.items()}, indent=1, sort_keys=True))
    os.replace(tmp, side)
    return side


def read_stamps_sidecar(path: Path) -> dict[str, str]:
    side = Path(path).with_name(Path(path).name + ".stamps.json")
    return json.loads(side.read_text()) if side.exists() else {}


def write_csv(df: pd.DataFrame, path: Path, **kw) -> Path:
    """Atomic CSV write (ledger sheets, review evidence)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_csv(tmp, index=False, **kw)
    os.replace(tmp, path)
    return path
