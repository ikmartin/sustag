"""Pinned downloads of ScienceBase item files, by stable file NAME.

Standalone helper -- NOT imported by the runtime read path. Used by the COMID-attribute builder to stage source CSV zips locally, then the builder reads only the pinned files (no live-fetch on any runtime path).

Why discover the URL by name each time: a ScienceBase file's direct URL is a content-addressed `?f=__disk__<hash>` link that CHURNS (the 2026-03 data-system migration rotated them; a hardcoded hash now 404s). The file NAME is stable, so we read the item JSON and resolve the current URL for that name at fetch time. The `sciencebase.usgs.gov/manager/.../file/...` "parquet" copies are also still broken (they return an HTML app shell) -- use the named CSV zips.
"""

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root on path

_UA = {"User-Agent": "Mozilla/5.0"}  # some ScienceBase clients get 403 without a browser UA
_ITEM_JSON = "https://www.sciencebase.gov/catalog/item/{item}"


def item_files(item: str, timeout: int = 60) -> list[dict]:
    """The item's file list from the ScienceBase JSON API ({name, size, url, ...} dicts)."""
    r = requests.get(_ITEM_JSON.format(item=item), params={"format": "json"}, headers=_UA, timeout=timeout)
    r.raise_for_status()
    return r.json().get("files", [])


def fetch_item_file(item: str, name: str, dest_dir: Path, force: bool = False, timeout: int = 300) -> Path:
    """Download `name` from ScienceBase `item` into dest_dir, pinned. Returns the local path.

    Idempotent: skips the download if the file is already present and non-empty (unless force). ScienceBase does not honor HTTP Range, so the whole file is streamed in one request.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / name
    if out.exists() and out.stat().st_size > 0 and not force:
        print(f"  pinned: {out.name} ({out.stat().st_size:,} B)")
        return out

    files = item_files(item, timeout=timeout)
    match = next((f for f in files if f.get("name") == name), None)
    if match is None:
        avail = ", ".join(f.get("name", "?") for f in files)
        raise FileNotFoundError(f"{name!r} not in ScienceBase item {item}. Available: {avail}")

    url = match["url"]
    print(f"  downloading {name} ({match.get('size', '?')} B) from item {item}...")
    tmp = out.with_suffix(out.suffix + ".part")
    with requests.get(url, headers=_UA, timeout=timeout, stream=True) as r:
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "text/html" in ctype:
            raise RuntimeError(f"{name}: got HTML ({ctype}) not a file -- ScienceBase URL likely stale/broken.")
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
    tmp.rename(out)  # atomic: only a complete download appears at the final path
    print(f"  pinned: {out.name} ({out.stat().st_size:,} B)")
    return out
