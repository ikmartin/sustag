"""CDL to per-catchment crop area, one row per (comid, year).

ALL EIGHTEEN YEARS IN ONE PASS. 2008-2024 share a single grid, so the coverage fractions -- the expensive half -- are computed once and applied to every year. Measured marginal cost is 0.08 ms per catchment per extra raster against 0.48 ms for the first, so looping years would cost roughly nine times as much for the same answer. 2025 is published on a LARGER grid (160171x105432 against 153811x96523) and therefore forms its own group; reusing 2024's transform for it would shift every 2025 count.

VALUES ARE AREAS, NOT TALLIES. A boundary pixel is split among the catchments it touches, so `corn_px` is a float: the number of pixels' worth of corn, at 900 m2 each. Fractions are deliberately NOT stored -- a fraction cannot be summed along the network, and every consumer of this table accumulates it upstream before dividing.

THE CLASS FILTER RUNS HERE, ON RAW CODES RETURNED BY THE ENGINE. `exactextract` hands back the distinct CDL codes present in each catchment with their coverage shares, so collapsing 256 codes into 8 classes is a post-processing step over a few dozen numbers per catchment rather than a remap of four billion pixels. `cdl_classes.py` is the only place the mapping lives.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config
from . import cdl_classes, zonal

YEAR_MIN, YEAR_MAX = 2008, 2026        # the water record's span; CDL supplies what it has

CDL_CRS = "EPSG:5070"


def rasters(directory) -> dict[int, object]:
    """`{year: path}` for every `{year}_30m_cdls.tif` in `directory`, within the water record's span."""
    out = {}
    for p in sorted(directory.glob("*_30m_cdls.tif")):
        try:
            y = int(p.name.split("_")[0])
        except ValueError:
            continue
        if YEAR_MIN <= y <= YEAR_MAX:
            out[y] = p
    return out


def grid_groups(paths: dict[int, object]) -> list[dict[int, object]]:
    """Split years into groups sharing one raster grid. Coverage is only reusable inside a group."""
    import rasterio

    groups: dict[tuple, dict] = {}
    for y, p in paths.items():
        with rasterio.open(p) as d:
            key = (d.width, d.height, tuple(np.round(np.asarray(d.transform)[:6], 3)), str(d.crs))
        groups.setdefault(key, {})[y] = p
    return list(groups.values())


def _reduce(comids: np.ndarray, res: pd.DataFrame, years: list[int]) -> pd.DataFrame:
    """One exactextract batch -> long rows of (comid, year, per-class pixel area, n_px).

    `{year}_frac` and `{year}_unique` are per-catchment arrays of the codes present and their share of that catchment's covered area; `{year}_count` is the covered area in pixel units. Multiplying gives area per code, which `class_index` then folds into the 8 columns.
    """
    lut = cdl_classes.class_index()
    K = len(cdl_classes.CLASSES)
    rows = []
    for y in years:
        counts = np.zeros((len(comids), K), dtype="float64")
        n_px = res[f"{y}_count"].to_numpy(dtype="float64")
        for i, (codes, frac) in enumerate(zip(res[f"{y}_unique"], res[f"{y}_frac"])):
            if codes is None or not len(codes):
                continue
            np.add.at(counts[i], lut[np.asarray(codes, dtype="int64")],
                      np.asarray(frac, dtype="float64") * n_px[i])
        df = pd.DataFrame(counts.astype("float32"),
                          columns=[f"{c.lower()}_px" for c in cdl_classes.CLASSES])
        df.insert(0, "year", np.int16(y))
        df.insert(0, "comid", comids)
        df["n_px"] = n_px.astype("float32")
        rows.append(df)
    return pd.concat(rows, ignore_index=True)


MANIFEST = "crops_manifest.json"


def _manifest_path(part_dir):
    return part_dir / MANIFEST


def _write_manifest(part_dir, groups: list[dict], n_batches: int) -> None:
    """Record which years each part directory covers, so a later run knows the shape without the rasters."""
    import json

    m = {"n_batches": n_batches,
         "groups": {f"crops_{min(g)}_{max(g)}": sorted(int(y) for y in g) for g in groups}}
    p = _manifest_path(part_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=2))
    tmp.replace(p)


def years_available(product: str = "crops") -> list[int]:
    """Every CDL year the archive holds, read from its listing rather than by expanding it."""
    from . import archive

    out = []
    for name in archive.member_names(product):
        try:
            y = int(name.split("_")[0])
        except ValueError:
            continue
        if YEAR_MIN <= y <= YEAR_MAX:
            out.append(y)
    return sorted(set(out))


def cached(part_dir, n_items: int, size: int = zonal.BATCH):
    """The finished frame if every batch of every group is checkpointed, else None. Opens no raster.

    THE POINT IS TO NOT EXPAND 50 GB TO READ NOTHING. Enumerating the CDL years means opening eighteen GeoTIFFs to compare their transforms, so a pass that needs no computation would still have needed the archive -- which is how a fully checkpointed crops step came to demand an unzip that the disk could not fit. The manifest records the group structure the last pass found, so completeness is answerable from `parts/` alone.
    """
    import json
    import math

    p = _manifest_path(part_dir)
    if not p.exists():
        return None
    m = json.loads(p.read_text())
    if int(m.get("n_batches", -1)) != math.ceil(n_items / size):
        return None                      # the cohort changed; the parts describe different catchments
    frames = []
    for name in m.get("groups", {}):
        d = part_dir / name
        want = [d / f"part_{i:05d}.parquet" for i in range(m["n_batches"])]
        if not all(f.exists() for f in want):
            return None
        frames.extend(want)
    if not frames:
        return None
    have = {y for ys in m.get("groups", {}).values() for y in ys}
    avail = set(years_available())
    if avail and avail != have:
        print(f"  crops: the archive now holds {sorted(avail - have) or 'fewer'} year(s) the "
              f"checkpoints do not cover -- recomputing", flush=True)
        return None
    print(f"  crops: every batch checkpointed ({len(frames)} parts over {len(m['groups'])} "
          f"grid group(s), years {min(have)}-{max(have)}); the CDL archive is not needed", flush=True)
    return pd.concat([pd.read_parquet(f) for f in frames], ignore_index=True)


def build(cat: zonal.Catchments, directory, limit: int | None = None,
          part_dir=None, force: bool = False) -> pd.DataFrame:
    """Every available CDL year over every catchment. Returns the long (comid, year, ...) frame.

    `directory` may be a path or an `archive.Lazy`; the archive is expanded only if a batch actually has to be computed. `part_dir` checkpoints per batch, which matters here more than anywhere else in the build: this is the one pass that cannot be repeated cheaply, and the CDL archive it reads is deleted afterwards.
    """
    if part_dir is not None and not force and limit is None:
        done = cached(part_dir, len(cat))
        if done is not None:
            return done
    directory = getattr(directory, "path", directory)
    paths = rasters(directory)
    if not paths:
        raise FileNotFoundError(f"no CDL rasters in {directory}")
    groups = grid_groups(paths)
    print(f"  {len(paths)} CDL year(s) {min(paths)}-{max(paths)} in {len(groups)} grid group(s): "
          f"{[sorted(g) for g in groups]}", flush=True)

    out = []
    for gi, group in enumerate(groups, 1):
        years = sorted(group)
        out.append(zonal.extract(
            cat, {str(y): group[y] for y in years}, CDL_CRS,
            ops=zonal.CATEGORICAL_OPS,
            reduce=lambda c, r, ys=years: _reduce(c, r, ys),
            limit=limit, force=force,
            part_dir=None if part_dir is None else part_dir / f"crops_{years[0]}_{years[-1]}",
            label=f"crops group {gi}/{len(groups)} ({years[0]}-{years[-1]}) "))
    if part_dir is not None and limit is None:
        import math

        _write_manifest(part_dir, [sorted(g) for g in groups], math.ceil(len(cat) / zonal.BATCH))
    return pd.concat(out, ignore_index=True)
