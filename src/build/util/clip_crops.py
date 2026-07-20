"""Download and clip the Crop Data Layer data to a bounding box.

Ported from the legacy data/crops/clip_crops.py. Standalone preprocessing tool -- NOT imported by
_make_crops.py, which simply assumes the clips already exist. Run this first (whenever you add
years) to build or refresh that cache. Two sources of national rasters:

  1. Manual national files (preferred for the long time series): download the CONUS CDL GeoTIFFs
     from the NASS National Download page and unzip them into src/data/raw/crops/national/:
         https://www.nass.usda.gov/Research_and_Science/Cropland/Release/
             datasets/{YEAR}_30m_cdls.zip      (2008-2025, 30 m)
     each unzips to a file like  {YEAR}_30m_cdls.tif  (CONUS, EPSG:5070).

  2. Scripted CropScape download (--download): request a server-side regional clip from the NASS
     GetCDLFile service and stream it (with resume) into src/data/raw/crops/downloaded/.

Either way the national/downloaded raster is windowed down to the shared region bbox
(config [region].bbox_wgs84, deliberately larger than the basins so a basin edit never forces a
re-clip) and cached as cdl_clip_{year}.tif (class codes, EPSG:5070). raw/crops/ is gitignored, so
this is only needed to *regenerate* crops from scratch -- the committed crops_global.parquet
supersedes it for normal use.

Usage
-----
    python -m src.build.util.clip_crops                 # clip every national file found
    python -m src.build.util.clip_crops --year 2020     # just one year (repeatable)
    python -m src.build.util.clip_crops --download       # download missing years from CropScape
    python -m src.build.util.clip_crops --force          # re-clip even if cached
"""

import requests
import xml.etree.ElementTree as ET
import argparse
import os
import re
import sys
import time
from pathlib import Path

import rasterio
from rasterio.windows import from_bounds
from rasterio.warp import transform_bounds

_THIS_DIR = Path(__file__).resolve().parent  # src/build/util
_SRC = _THIS_DIR.parents[1]  # src
_RAW_DIR = _SRC / "data" / "raw" / "crops"
_NATIONAL_DIR = _RAW_DIR / "national"  # put manually-downloaded national .tif files here
_DOWNLOAD_DIR = _RAW_DIR / "downloaded"  # raw CropScape downloads, before clipping
_CLIP_DIR = _RAW_DIR / "clipped"  # cached regional clips (what _make_crops reads)

sys.path.insert(0, str(_THIS_DIR.parents[2]))  # repo root -> import src.build.config
from src.build.config import get_region_bbox, get_config

# ── Region to keep, in WGS84 (lon/lat) ───────────────────────────────────────
# Shared region from pipeline_config.toml [region].bbox_wgs84 as
# (min_lon, min_lat, max_lon, max_lat). Edit that file to adjust the clip margin.
BBOX_WGS84 = get_region_bbox()

# Filenames encode year and resolution: {year}_{res}m_cdls.
_FNAME_RE = re.compile(r"(\d{4})_(\d+)m_cdls", re.IGNORECASE)


def _human(nbytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024 or unit == "GB":
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024


def _clip_path(year: int) -> Path:
    return _CLIP_DIR / f"cdl_clip_{year}.tif"


def _download_path(year: int) -> Path:
    return _DOWNLOAD_DIR / f"cdl_download_{year}.tif"


def find_national_files() -> dict[int, Path]:
    """Map year -> national CDL GeoTIFF found anywhere under raw/crops/national/.

    Accepts both 30 m and 10 m files; if both exist for a year, prefers 30 m
    (keeps the time series at one resolution). Searches recursively because the
    NASS zips unzip into a per-year subfolder.
    """
    found: dict[int, Path] = {}
    if not _NATIONAL_DIR.exists():
        return found
    for path in _NATIONAL_DIR.rglob("*_cdls.tif"):
        m = _FNAME_RE.search(path.name)
        if not m:
            continue
        year, res = int(m.group(1)), int(m.group(2))
        if year in found and res != 30:  # prefer 30 m when a year has both
            continue
        found[year] = path
    return found


def clip_to_bbox(src_path: Path, out_path: Path) -> tuple[int, int]:
    """Window a national CDL raster down to BBOX_WGS84 and write it out.

    Returns (width, height) of the clip. Preserves CRS, class codes, nodata and
    the CDL colormap; compresses output with LZW.
    """
    with rasterio.open(src_path) as src:
        # Reproject the WGS84 box into the raster's CRS (EPSG:5070). densify_pts
        # follows the curved Albers edges so the envelope safely encloses the box.
        left, bottom, right, top = transform_bounds("EPSG:4326", src.crs, *BBOX_WGS84, densify_pts=21)
        window = from_bounds(left, bottom, right, top, src.transform).round_offsets().round_lengths()

        # The source must fully contain the clip region. A smaller source (e.g. a
        # partial download) would otherwise be silently clamped to a too-small
        # clip, breaking the uniform extent every clip is meant to share.
        if (
            window.col_off < 0
            or window.row_off < 0
            or window.col_off + window.width > src.width
            or window.row_off + window.height > src.height
        ):
            raise ValueError(
                f"Clip region is not fully contained in source '{src_path.name}'. "
                f"The source raster is smaller than the region {BBOX_WGS84} — "
                f"re-download the full region before clipping."
            )

        data = src.read(window=window)
        transform = src.window_transform(window)
        profile = src.profile.copy()
        profile.update(
            height=int(window.height),
            width=int(window.width),
            transform=transform,
            compress="lzw",
        )
        try:
            colormap = src.colormap(1)
        except ValueError:
            colormap = None

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(data)
        if colormap:
            dst.write_colormap(1, colormap)
    return int(window.width), int(window.height)


def _stream_with_resume(url: str, tmp_path: Path, max_retries: int = 6, chunk_size: int = 1 << 20) -> None:
    """Stream a download to tmp_path, retrying with backoff and resuming from
    wherever a dropped connection left off (NASS's GetCDLFile cache files
    support Range requests since they're served as plain static files).

    If the server ever ignores our Range header and sends a fresh 200 instead
    of a 206, we detect that and restart tmp_path from scratch rather than
    silently corrupting it by appending mismatched bytes.
    """
    for attempt in range(1, max_retries + 1):
        existing = tmp_path.stat().st_size if tmp_path.exists() else 0
        headers = {"Range": f"bytes={existing}-"} if existing else {}
        mode = "ab" if existing else "wb"

        try:
            with requests.get(url, headers=headers, timeout=60, stream=True) as resp:
                resp.raise_for_status()

                # Server ignored our Range request and is sending the whole file
                # again — discard what we had so we don't append onto it.
                if existing and resp.status_code != 206:
                    print(f"  server did not honor resume (status {resp.status_code}); restarting download")
                    existing = 0
                    mode = "wb"

                total = int(resp.headers.get("content-length", 0)) + existing
                with open(tmp_path, mode) as f:
                    downloaded = existing
                    for chunk in resp.iter_content(chunk_size=chunk_size):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            pct = downloaded / total * 100 if total else 0
                            print(f"\r  {downloaded / 1e6:,.1f} MB / {total / 1e6:,.1f} MB ({pct:.1f}%)", end="")
            print()
            return  # success

        except (
            requests.exceptions.ChunkedEncodingError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as e:
            print(f"\n  attempt {attempt}/{max_retries} failed: {e}")
            if attempt == max_retries:
                raise
            wait = min(2**attempt, 60)
            print(
                f"  retrying in {wait}s (resuming from {tmp_path.stat().st_size if tmp_path.exists() else 0} bytes)..."
            )
            time.sleep(wait)


def _get_with_retry(url: str, max_retries: int = 5, timeout: int = 300, **kwargs) -> requests.Response:
    """GET with retries/backoff and a generous timeout.

    Used for the GetCDLFile metadata request, which makes NASS generate the
    clip server-side before it can respond — for larger regions that can take
    well over a minute, so this needs both a longer timeout than a normal
    request and the ability to just try again if it times out anyway.
    """
    for attempt in range(1, max_retries + 1):
        try:
            return requests.get(url, timeout=timeout, **kwargs)
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
            print(f"  attempt {attempt}/{max_retries} failed: {e}")
            if attempt == max_retries:
                raise
            wait = min(2**attempt, 60)
            print(f"  retrying in {wait}s...")
            time.sleep(wait)


def download_source(year: int) -> Path | None:
    """Download a regional CDL clip for `year` from the NASS CropScape GetCDLFile service into
    raw/crops/downloaded/, returning the raster path (or None on failure). Skips the (slow, flaky)
    download when the raw file is already on disk -- delete it to force a fresh download."""
    raw_path = _download_path(year)

    if not raw_path.exists():
        minx, miny, maxx, maxy = get_region_bbox(albers=True)
        albers_bbox = f"{minx:.0f},{miny:.0f},{maxx:.0f},{maxy:.0f}"
        url = f"https://nassgeodata.gmu.edu/axis2/services/CDLService/GetCDLFile?year={year}&bbox={albers_bbox}"

        # Step 1: request XML response containing the GeoTIFF download URL. NASS
        # builds the clip on-demand here, which can be slow for larger regions —
        # give it a long timeout and a few retries rather than failing at 60s.
        resp = _get_with_retry(url)

        if resp.status_code == 500:
            print("500 Server Error — common causes:")
            print("  - bbox outside continental US")
            print("  - CDL not available for this year/region")
            print(f"Raw response: {resp.text[:300]}")
            return None

        if resp.status_code != 200:
            print(f"ERROR: unexpected status {resp.status_code} from CDL service.")
            print(f"Raw response: {resp.text[:400]}")
            return None

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as e:
            print(f"ERROR: CDL response was not valid XML ({e}).")
            print(f"Status code: {resp.status_code}")
            print(f"Content-Type: {resp.headers.get('content-type')}")
            print(f"Raw response (first 500 chars): {resp.text[:500]!r}")
            return None

        # The download URL is in a <returnURL> element. {*} matches the element in
        # any namespace or none.
        url_elem = root.find(".//{*}returnURL")
        if url_elem is None or not url_elem.text:
            print("ERROR: Could not parse GeoTIFF URL from CDL response.")
            print(f"Raw response: {resp.text[:400]}")
            return None

        tiff_url = url_elem.text.strip()
        print(f"GeoTIFF URL: {tiff_url}\n")

        # Step 2: stream the raw download to a temp name and rename on success, so an
        # interrupted download never leaves a truncated file that looks complete.
        tmp_path = raw_path.with_suffix(".tif.part")
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        _stream_with_resume(tiff_url, tmp_path)
        os.replace(tmp_path, raw_path)  # atomic; the final file only appears once complete
    else:
        print(f"  {year}: using existing download {raw_path.name}")

    return raw_path


def main(force: bool = False, years: list = None, download: bool = False) -> None:
    if years is None:
        cfg = get_config()["crops"]
        years = set(range(cfg["year_start"], cfg["year_end"] + 1))
    years = set(years)

    if BBOX_WGS84 is None:
        raise ValueError("Set [region].bbox_wgs84 in pipeline_config.toml before running.")

    done = [y for y in sorted(years) if _clip_path(y).exists()]
    if done and not force:
        print(f"Clips already exist for {done} in {_CLIP_DIR.relative_to(_SRC.parent)}. Use --force to rebuild.\n")
        years -= set(done)

    _CLIP_DIR.mkdir(parents=True, exist_ok=True)
    files = find_national_files()
    missing = sorted(years - set(files))
    if missing and not download:
        print(f"No national CDL .tif for years {missing} under {_NATIONAL_DIR.relative_to(_SRC.parent)}.")
        print("  Download + unzip the national rasters there (e.g. <YEAR>_30m_cdls.tif from")
        print("  https://www.nass.usda.gov/Research_and_Science/Cropland/Release/index.php),")
        print("  or rerun with --download to fetch those years from CropScape.\n")

    def clip_and_save(year: int, file_to_clip: Path) -> None:
        out = _clip_path(year)
        if out.exists() and not force:
            print(f"  {year}: exists already ({_human(out.stat().st_size)})")
            return
        w, h = clip_to_bbox(file_to_clip, out)
        print(f"  {year}: {file_to_clip.name} -> {out.name}  ({w}x{h} px, {_human(out.stat().st_size)})")

    print(f"Region (WGS84): {BBOX_WGS84}")
    print(f"Clipping {len(years)} file(s) to this region.")
    for year in sorted(years):
        if year in files:  # a manual national file to clip
            clip_and_save(year, files[year])
        elif download:  # fetch a server-side regional clip from CropScape
            src = download_source(year)
            if src is not None:
                clip_and_save(year, src)
        else:
            print(f"  {year}: skipped (no national file; rerun with --download).")

    print("\nDone.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="Re-clip even if the cached clip exists.")
    parser.add_argument("--download", action="store_true", help="Download missing years from CropScape.")
    parser.add_argument(
        "--year",
        type=int,
        action="append",
        metavar="YYYY",
        help="Process only this year (repeatable, e.g. --year 2020 --year 2021).",
    )
    args = parser.parse_args()
    main(force=args.force, years=args.year, download=args.download)
