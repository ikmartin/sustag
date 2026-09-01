"""Sekellick & Sherr's county fertiliser and manure, already downscaled to NHDPlus COMID.

WHY THIS SOURCE AND NOT ANOTHER IN THE FAMILY. Brakebill & Gronberg and Gronberg & Arnold are *upstream* of gTREND rather than independent of it, so ingesting them adds a second copy of one measurement. What this release uniquely carries is the **confined versus unconfined manure split** -- CAFO against pasture -- which nothing else on disk provides at COMID grain, and which matters here because the Tier 2 battery found `livestock_hogs` one of only two nutrient components carrying signal the rest of the pool does not imply.

NO GEOMETRY IS INVOLVED. The release is already keyed on COMID, so this is an acquisition and a filter, not an overlay -- unlike the karst map, which needs a real polygon intersection.

FOUR CENSUS YEARS ONLY (2002, 2007, 2012, 2017), because the underlying USDA Census runs five-yearly. Like every source in this family it stops at 2017 against a water record running to 2026; it is a slowly-varying descriptor, never contemporaneous, and the post-2017 gap is structural rather than an artefact of what we chose to fetch.
"""

from __future__ import annotations

import pandas as pd

from .. import config, verify
from .. import io as bio

ITEM = "65787037d34e952b22746538"
BASE = f"https://www.sciencebase.gov/catalog/item/{ITEM}?format=json"

# The nitrogen files only. The release also ships the phosphorus triplet and two `de_` (delivered) files;
# taking all nine would be 728 MB for 279 MB of what this project models.
WANTED = {
    "ag_tn_cman.csv": "cman",       # confined manure -- the CAFO axis
    "ag_tn_uman.csv": "uman",       # unconfined manure -- pasture
    "ag_tn_fert.csv": "fert",
}
CENSUS_YEARS = (2002, 2007, 2012, 2017)


def _path():
    return config.ACQ_ATTRIBUTES / "sekellick_n.parquet"


def _file_urls() -> dict:
    import json
    import urllib.request

    with urllib.request.urlopen(BASE, timeout=120) as r:
        d = json.load(r)
    return {f["name"]: f["url"] for f in d.get("files", []) if f.get("name") in WANTED}


def fetch(force: bool = False) -> int:
    """Download the three nitrogen files, clip to AOE COMIDs, write one parquet. Returns rows written."""
    import io as _io
    import urllib.request

    from . import network

    p = _path()
    if p.exists() and not force:
        return 0
    import pyarrow.parquet as pq

    keep = set(pq.read_table(network._path("catchments"),
                             columns=["FEATUREID"]).column("FEATUREID").to_pylist())
    urls = _file_urls()
    missing = sorted(set(WANTED) - set(urls))
    if missing:
        raise RuntimeError(f"release is missing {missing}; the item may have been revised")

    out = None
    for name, short in WANTED.items():
        with urllib.request.urlopen(urls[name], timeout=900) as r:
            raw = r.read()
        d = pd.read_csv(_io.BytesIO(raw))
        d = d.rename(columns={"COMID": "comid"})
        d = d[d.comid.isin(keep)]
        cols = {c: f"{short}_{c.rsplit('_', 1)[1]}" for c in d.columns if c != "comid"}
        d = d.rename(columns=cols)
        print(f"    {name}: {len(d):,} AOE reaches, {len(cols)} year(s)", flush=True)
        out = d if out is None else out.merge(d, on="comid", how="outer")
    if out is None or not len(out):
        return 0
    p.parent.mkdir(parents=True, exist_ok=True)
    bio.write_parquet(out, p, stamps={"aoe": config.aoe_stamp(), "source": "sekellick_2024"})
    return len(out)


def main(force: bool = False) -> None:
    n = fetch(force=force)
    print(f"  sekellick: {n:,} reaches" if n else "  sekellick: cached", flush=True)


@verify.check("a3", "the Sekellick manure split is present and carries both manure classes")
def _check_sekellick():
    """The confined/unconfined split is the only reason this source is held; a file with one of them is useless."""
    import pyarrow.parquet as pq

    if not _path().exists():
        return None, "not acquired"
    names = set(pq.ParquetFile(_path()).schema_arrow.names)
    want = {f"{s}_{y}" for s in ("cman", "uman", "fert") for y in CENSUS_YEARS}
    gap = sorted(want - names)
    n = pq.ParquetFile(_path()).metadata.num_rows
    return not gap, (f"{n:,} reaches, all {len(want)} (class, year) columns"
                     if not gap else f"missing {gap[:4]}")
