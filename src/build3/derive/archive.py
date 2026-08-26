"""Expand a national archive, use it, delete the expansion. The zip is what persists.

TWO PRODUCTS FOLLOW THIS RULE AND TWO DO NOT. CDL is 24 GB zipped against 50 GB expanded and AgTile 126 MB against 15 GB, so for those the expansion is scratch and the zip is the artifact. Surplus and fertilizer are already LZW float32 -- they barely compress, and at 4.1 GB and 431 MB they stay expanded permanently. `EPHEMERAL` is the list, and it is short on purpose.

NOTHING ABOUT THE LAYOUT IS HARD-CODED, because the layout is not stable: CDL's archive is `crops/national.zip` holding a `national/` directory, AgTile's is `agtile/national/national.zip` holding an `AgTile-US-TIFF/` one. So the zip is FOUND under the product directory and the expansion's name is READ FROM ITS OWN LISTING. A table of paths would have been correct for exactly as long as it took to repackage one archive.

`/vsizip/` IS NOT AN ALTERNATIVE for CDL. Its members are Deflate, so a windowed read decompresses from the start of a 3 GB member every time. Expanding once and reading a tiled GeoTIFF is the difference between a fifty-minute pass and an overnight one.

AN EXPANSION IS DELETED BECAUSE IT CAN BE REBUILT, NOT BECAUSE THIS STEP MADE IT. `expanded()` removes the directory for every EPHEMERAL product once the pass is done, including one that was already there when it started -- that is what the pattern is for, and an earlier "only delete what you created" rule left 50 GB of CDL on a disk with 46 GB free. The guard that makes this safe is the archive: deletion is refused when `find_zip` finds nothing, because then the expansion is the only copy.

DISK IS CHECKED BEFORE THE UNZIP, NOT DISCOVERED DURING IT. A half-written 50 GB expansion is worse than a refusal: it looks like a valid directory to the next run and has to be found and removed by hand.
"""

from __future__ import annotations

import shutil
import zipfile
from contextlib import contextmanager
from pathlib import Path

from .. import config

# Products whose expansion is scratch. Everything else stays expanded on disk.
EPHEMERAL = ("crops", "agtile")

# Headroom above the archive's own expanded size, so the unzip is not the thing that fills the disk.
FREE_MARGIN_GB = 5.0

_IGNORE = ("__MACOSX",)


def product_dir(product: str) -> Path:
    return config.RAW / product


def find_zip(product: str) -> Path | None:
    """The product's archive, wherever it sits under `raw/{product}/`. `national.zip` wins a tie."""
    zips = [p for p in sorted(product_dir(product).rglob("*.zip")) if not p.name.startswith(".")]
    if not zips:
        return None
    return next((p for p in zips if p.stem == "national"), zips[0])


def _top_level(z: Path) -> str:
    """The single directory the archive expands into, from its listing."""
    tops = {n.split("/")[0] for n in zipfile.ZipFile(z).namelist()
            if n.split("/")[0] not in _IGNORE and n.strip()}
    if len(tops) != 1:
        raise RuntimeError(f"{z.name} expands into {sorted(tops)}; expected exactly one directory")
    return tops.pop()


def expanded_dir(product: str) -> Path | None:
    """Where the rasters are, or would be. The archive's own top-level name beside the zip; else a `*.tif` directory already on disk."""
    z = find_zip(product)
    if z is not None:
        return z.parent / _top_level(z)
    for d in sorted(product_dir(product).rglob("*")):
        if d.is_dir() and any(d.glob("*.tif")):
            return d
    return None


def member_names(product: str) -> list[str]:
    """The file names `product` holds, from the ARCHIVE'S LISTING or the expansion -- without expanding.

    A zip's central directory is metadata: reading it costs nothing and answers "which years are in here", which is what lets a checkpointed pass verify that no new input has appeared since it ran. Falling back to a glob when the product is expanded keeps the two routes interchangeable.
    """
    d = expanded_dir(product)
    if d is not None and d.exists() and any(d.rglob("*.tif")):
        return sorted(f.name for f in d.rglob("*.tif"))
    z = find_zip(product)
    if z is None:
        return []
    with zipfile.ZipFile(z) as f:
        return sorted(n.split("/")[-1] for n in f.namelist()
                      if n.endswith(".tif") and not n.split("/")[-1].startswith("._"))


def _expanded_size_gb(z: Path) -> float:
    """Uncompressed size, from the archive's own directory listing -- no read, no estimate."""
    with zipfile.ZipFile(z) as f:
        return sum(i.file_size for i in f.infolist()) / 1e9


def free_gb(path=None) -> float:
    return shutil.disk_usage(path or config.RAW).free / 1e9


def present(product: str) -> tuple[bool, str]:
    """Is this product usable, and by which route? `(ok, reason)`.

    The presence check chunk 1 still owes: a product is fine if it is expanded OR if its archive is there, and anything else is a missing input rather than a chunk-4 failure.
    """
    d = expanded_dir(product)
    if d is not None and d.exists() and any(d.rglob("*.tif")):
        return True, f"expanded ({d.relative_to(config.RAW)})"
    z = find_zip(product)
    if z is not None:
        return True, f"zipped ({z.relative_to(config.RAW)}, {z.stat().st_size/1e9:.1f} GB -> " \
                     f"{_expanded_size_gb(z):.0f} GB)"
    return False, f"no *.tif and no *.zip under {product_dir(product)}"


class Lazy:
    """A handle to a product's rasters that expands the archive only when something asks for the path.

    `.path` is what triggers the unzip. A pass whose work is entirely checkpointed never touches it, so it never pays 50 GB and 4 minutes to read nothing -- which is exactly what chunk 5 was doing: `run_chunk5` opened the crops archive before `crops.build` had a chance to notice that all 150 batches were already on disk.
    """

    def __init__(self, product: str):
        self.product = product
        self._path = None
        self.used = False

    @property
    def path(self):
        self.used = True
        if self._path is None:
            self._path = _resolve(self.product)
        return self._path


@contextmanager
def lazy(product: str, keep: bool = False):
    """`Lazy` handle for `product`, cleaned up on exit only if its `.path` was ever taken.

    An expansion nobody looked at is left alone. The delete exists to reclaim what THIS pass needed, and quietly removing 50 GB from a run that turned out to have no work to do would be a surprise, not a policy.
    """
    h = Lazy(product)
    try:
        yield h
    finally:
        if h.used:
            _cleanup(product, h._path, keep=keep)


@contextmanager
def expanded(product: str, keep: bool = False):
    """Yield the directory holding `product`'s rasters, expanding the archive if needed.

    Deletes the expansion afterwards for every `EPHEMERAL` product, WHETHER OR NOT this call created it. Exceptions still clean up -- a failed pass must not leave 50 GB behind -- and `keep=True` overrides for debugging.

    THE TEST IS "CAN IT BE REBUILT", NOT "DID I BUILD IT". An earlier version only removed what it had expanded itself, on the principle that a step should not delete files it did not create. That is a sound instinct and it was the wrong rule here: CDL was already expanded before chunk 5 first ran, so the pass took the reuse branch, returned early, and left 50 GB on a disk with 46 GB free -- defeating the entire point of the pattern. Ownership is not what makes a deletion safe; recoverability is. So the guard is that `find_zip` must return an existing archive, and the expansion is refused deletion when it does not, because then it is the only copy.
    """
    d = _resolve(product)
    try:
        yield d
    finally:
        _cleanup(product, d, keep=keep)


def _resolve(product: str) -> Path:
    """The directory holding `product`'s rasters, expanding the archive if it is not already there."""
    d = expanded_dir(product)
    if d is not None and d.exists() and any(d.rglob("*.tif")):
        print(f"  {product}: using the expansion already on disk ({d})", flush=True)
        return d

    z = find_zip(product)
    if z is None:
        raise FileNotFoundError(f"{product}: no expansion and no archive under {product_dir(product)}")
    need, free = _expanded_size_gb(z), free_gb()
    if free < need + FREE_MARGIN_GB:
        raise RuntimeError(
            f"{product}: expanding {z.name} needs {need:.0f} GB (+{FREE_MARGIN_GB:.0f} GB margin) "
            f"and only {free:.0f} GB is free. Free space first -- a partial expansion looks valid "
            f"to the next run.\n"
            f"  Finder will report more than {free:.0f} GB: macOS counts PURGEABLE space -- Time "
            f"Machine local snapshots and evictable caches -- as available, and an unzip cannot rely "
            f"on it. `tmutil listlocalsnapshots /` shows them; this figure is what the filesystem "
            f"will actually hand out today.")
    print(f"  {product}: expanding {z.name} -> {need:.0f} GB ({free:.0f} GB free) ...", flush=True)
    with zipfile.ZipFile(z) as f:
        f.extractall(z.parent)
    d = z.parent / _top_level(z)
    if not d.exists():
        raise RuntimeError(f"{product}: expanded {z.name} but {d} is not there")
    return d


def _cleanup(product: str, d, keep: bool = False) -> None:
    """Remove an EPHEMERAL product's expansion, unless it is the only copy or `keep` says otherwise."""
    if d is None:
        return
    z = find_zip(product)
    if product not in EPHEMERAL or keep:
        print(f"  {product}: keeping {d}", flush=True)
    elif z is None or not z.exists():
        print(f"  {product}: KEEPING {d} -- there is no archive to rebuild it from, so this "
              f"expansion is the only copy", flush=True)
    else:
        size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / 1e9
        shutil.rmtree(d, ignore_errors=True)
        mac = z.parent / "__MACOSX"
        if mac.exists():
            shutil.rmtree(mac, ignore_errors=True)
        print(f"  {product}: removed the expansion ({size:.0f} GB reclaimed), {z.name} stays "
              f"-- {free_gb():.0f} GB free", flush=True)
