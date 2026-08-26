"""CDL byte code -> agronomic class. The table, and nothing else -- this is the file to edit.

CDL ships 256 codes and the model wants a handful of classes, so the codes are collapsed before anything is counted. Changing a class is a one-line edit here; no aggregation code lives in this file, and `crops.py` never hard-codes a class name.

CARRIED OVER UNCHANGED from `src/data/cdl_legend.py::_cdl_to_class`, the last build's filter. It is a PORT, not an import: `cdl_legend` sits on the read side of the build/data split (`access.lookup_crop` imports it) and chunk 5 does not reach across that line. The two cannot silently disagree, because this mapping is applied ONCE at build time and its result is baked into the column names of `crops.parquet` -- the read layer consumes columns, never codes, so a re-classification is a rebuild and announces itself.

The groupings are agronomic rather than botanical, and each exists for a nitrogen reason:

    Corn          the fertilised crop, plus sweet/pop corn and every corn-containing double crop
    Soybeans      including soy double crops that carry no corn
    Alfalfa       split out from hay: an N-fixer that mineralises after termination
    Hay_Pasture   perennial living cover, low leaching
    Small_Grains  lower N rotation crops; also the closest thing CDL gives to a cover-crop signal
    Fallow        idle and bare, near-zero input
    Nonag         developed, forest, wetland, water, barren -- ~zero fertiliser N
    Other         the default for anything unlisted, which is mostly specialty crops
"""

from __future__ import annotations

import numpy as np

CODE_TO_CLASS = {
    # Corn (incl. corn-containing double crops, sweet/pop corn)
    1: "Corn", 12: "Corn", 13: "Corn", 225: "Corn", 226: "Corn", 235: "Corn", 241: "Corn",
    # Soybeans (incl. soy-containing double crops w/o corn)
    5: "Soybeans", 239: "Soybeans", 240: "Soybeans", 254: "Soybeans",
    # Alfalfa (split out; N-fixer w/ post-termination mineralization)
    36: "Alfalfa",
    # Perennial hay / pasture (low-leaching living cover)
    37: "Hay_Pasture", 59: "Hay_Pasture", 176: "Hay_Pasture",
    # Small grains (lower N, rotation crops) -- incl. weak cover-crop proxies
    21: "Small_Grains", 22: "Small_Grains", 23: "Small_Grains", 24: "Small_Grains",
    25: "Small_Grains", 27: "Small_Grains", 28: "Small_Grains", 29: "Small_Grains",
    30: "Small_Grains", 39: "Small_Grains", 205: "Small_Grains",
    # Fallow / idle (near-zero input, bare)
    61: "Fallow",
    # Non-agricultural (~zero fertilizer N)
    63: "Nonag", 64: "Nonag", 65: "Nonag", 81: "Nonag", 82: "Nonag", 83: "Nonag",
    87: "Nonag", 88: "Nonag", 92: "Nonag", 111: "Nonag", 112: "Nonag", 121: "Nonag",
    122: "Nonag", 123: "Nonag", 124: "Nonag", 131: "Nonag", 141: "Nonag", 142: "Nonag",
    143: "Nonag", 152: "Nonag", 190: "Nonag", 195: "Nonag",
}

DEFAULT = "Other"

# Sorted, so the column order of `crops.parquet` is a function of the table above and not of insertion order.
CLASSES = tuple(sorted(set(CODE_TO_CLASS.values()) | {DEFAULT}))

# CDL codes that are cultivated cropland. The denominator AgTile needs -- its raster is a bare 1/0 tile flag with no cropland mask of its own, so `tile_frac_ag` has to be denominated against this.
CULTIVATED = tuple(sorted(c for c, k in CODE_TO_CLASS.items()
                          if k in ("Corn", "Soybeans", "Alfalfa", "Small_Grains", "Fallow")))


def code_to_class(code: int) -> str:
    return CODE_TO_CLASS.get(int(code), DEFAULT)


def class_index() -> np.ndarray:
    """256-entry lookup: CDL code -> position in `CLASSES`. Vectorises the remap over an array of codes."""
    pos = {c: i for i, c in enumerate(CLASSES)}
    return np.array([pos[code_to_class(c)] for c in range(256)], dtype=np.int16)
