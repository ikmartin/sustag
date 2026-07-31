"""Build processed/comid_attrs.parquet: static, COMID-keyed basin attributes.

Pipeline A of the static-data additions. One row per NHDPlus reach in the region, keyed by the integer COMID, carrying both the CAT_ (local incremental catchment) and TOT_ (upstream-accumulated) form of each attribute. Joined at feature time by the basin's outlet COMID (features.site_static); the TOT_ columns are the between-site model features, the CAT_ columns are carried for the reach- level choropleth / local-character diagnostics.

Sources are ScienceBase items from the USGS "Select Attributes for NHDPlusV2.1 Reach Catchments" release (Wieczorek et al., DOI 10.5066/F7765D7V). Each ships a `{NAME}_CONUS.zip` with a comma-delimited `{NAME}_CONUS.txt` whose header is `COMID, CAT_x, CAT_NODATA, ACC_x, ACC_NODATA, TOT_x, TOT_NODATA`. Adding an attribute is one ATTR_SOURCES row.

INGEST TRAPS handled here (see notes/future-work-report.md 6):
  - Parse the CSV HEADER, not the FGDC XML: the TILES92 XML mislabels TOT_TILES92 as ACC_TILES92.
    We pick columns by the CAT_/TOT_ prefix off the real header, so the mislabel is irrelevant.
  - The `.txt` members are COMMA-delimited despite the extension.
  - `-9999` in a value column = reach disconnected from the routing network -> coerced to NaN.
  - TILES92's NODATA columns are vacuously 0.0 (no coverage signal) -- we don't derive a mask.
  - The ScienceBase `?f=__disk__` URLs churn and the `manager/.../parquet` copies are broken HTML;
    fetch_sciencebase resolves the current URL by stable file NAME and pins the CSV zip.

Deferred (append an ATTR_SOURCES row + a parse hook): SPARROW (needs a column whitelist against calibration-leak flux_* columns, comid>0 filter, IncAreaKm2==0 guard, log-sentinel handling, and upstream accumulation since it is incremental-catchment not pre-accumulated), StreamCat, N-inputs.

Output: src/data/processed/comid_attrs.parquet (committed). Raw zips: src/data/raw/comid_attrs/ (gitignored).

Usage
-----
    python -m src.build._make_comid_attrs [--force]
"""

import argparse
import io
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

_THIS_DIR = Path(__file__).resolve().parent  # src/build
_SRC = _THIS_DIR.parent  # src
sys.path.insert(0, str(_SRC.parent))  # repo root on path

from src.build.util.fetch_sciencebase import fetch_item_file

_RAW_DIR = _SRC / "data" / "raw" / "comid_attrs"
_OUT_PATH = _SRC / "data" / "processed" / "comid_attrs.parquet"
_FLOWLINES = _SRC / "data" / "processed" / "map_overlays" / "iowa_flowlines.parquet"

_NODATA_SENTINEL = -9999


@dataclass
class AttrSource:
    """One ScienceBase COMID-attribute item. cat_col/tot_col are found by prefix, not hardcoded -- the header carries exactly one CAT_* (besides CAT_NODATA) and one TOT_* (besides TOT_NODATA)."""

    name: str  # -> output columns cat_{name}, tot_{name}
    item: str  # ScienceBase item id
    zip_name: str  # the {NAME}_CONUS.zip member of the item


ATTR_SOURCES = [
    AttrSource("tiles92", "57067469e4b03f95a075ad3e", "TILES92_CONUS.zip"),
    AttrSource("bfi", "5669a8e3e4b08895842a1d4f", "BFI_CONUS.zip"),
    AttrSource("contact", "56f96fc5e4b0a6037df06b12", "CONTACT_CONUS.zip"),
]


def _region_comids() -> set[int]:
    """Every COMID a site or deploy pin can snap to = the reaches in the flowlines layer."""
    fl = pd.read_parquet(_FLOWLINES, columns=["COMID"])
    return set(fl["COMID"].dropna().astype(int))


def _pick_prefixed(cols, prefix: str) -> str:
    """The single {prefix}* column that is not {prefix}NODATA. Fails loud if absent/ambiguous."""
    cands = [c for c in cols if c.upper().startswith(prefix) and not c.upper().endswith("NODATA")]
    if len(cands) != 1:
        raise ValueError(f"expected exactly one {prefix}* value column, found {cands} in {list(cols)}")
    return cands[0]


def _load_source(src: AttrSource, force: bool) -> pd.DataFrame:
    """Fetch + read one source, returning [comid, cat_{name}, tot_{name}] with -9999 -> NaN."""
    zip_path = fetch_item_file(src.item, src.zip_name, _RAW_DIR, force=force)
    with zipfile.ZipFile(zip_path) as z:
        member = next(n for n in z.namelist() if n.lower().endswith(".txt"))
        raw = z.read(member)
    # Comma-delimited despite the .txt extension. Parse the real header (never the mislabelled XML).
    df = pd.read_csv(io.BytesIO(raw))
    cat_col = _pick_prefixed(df.columns, "CAT_")
    tot_col = _pick_prefixed(df.columns, "TOT_")
    out = pd.DataFrame(
        {
            "comid": df["COMID"].astype(int),
            f"cat_{src.name}": pd.to_numeric(df[cat_col], errors="coerce"),
            f"tot_{src.name}": pd.to_numeric(df[tot_col], errors="coerce"),
        }
    )
    for c in (f"cat_{src.name}", f"tot_{src.name}"):
        out.loc[out[c] == _NODATA_SENTINEL, c] = float("nan")  # disconnected-from-network sentinel
    return out


def build_comid_attrs(force: bool = False) -> pd.DataFrame:
    if not _FLOWLINES.exists():
        raise FileNotFoundError(f"{_FLOWLINES} missing; run `python -m src.build._make_map_overlays` first.")
    region = _region_comids()
    print(f"Region reaches (from flowlines): {len(region):,} COMIDs")

    merged = None
    for src in ATTR_SOURCES:
        df = _load_source(src, force)
        df = df[df["comid"].isin(region)]  # filter to region -- covers every snap-able reach
        print(f"  {src.name}: {len(df):,} region rows")
        merged = df if merged is None else merged.merge(df, on="comid", how="outer")

    merged = merged.sort_values("comid", ignore_index=True)
    _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(_OUT_PATH, index=False)
    attr_cols = [c for c in merged.columns if c != "comid"]
    print(f"comid_attrs.parquet: {len(merged):,} reaches x {len(attr_cols)} attrs {attr_cols}")
    return merged


def main(force: bool = False) -> None:
    build_comid_attrs(force=force)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="Re-download source zips even if pinned.")
    args = ap.parse_args()
    build_comid_attrs(force=args.force)
    print(f"Wrote {_OUT_PATH}")
