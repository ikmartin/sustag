"""
Iowa Historic Rainfall Time Series Builder
Source: IEM (Iowa Environmental Mesonet) — https://mesonet.agron.iastate.edu/rainfall/

Dependencies:
    pip install geopandas pandas requests tqdm pyarrow shapely

Usage:
    python iowa_rainfall_pipeline.py
co
Outputs:
    - data/raw/         → downloaded daily shapefiles (zips)
    - data/iowa_rainfall_daily.parquet  → clean stacked time series
    - data/iowa_rainfall_county.parquet → county-level aggregated version (optional)
"""

import os
import io
import zipfile
import requests
import pandas as pd
import geopandas as gpd
from datetime import date, timedelta
from pathlib import Path
from tqdm import tqdm


# ── Configuration ────────────────────────────────────────────────────────────

START_DATE = date(2015, 1, 1)
END_DATE   = date(2015, 12, 31)

# Projection: "4326" (WGS84 lat/lon) or "26915" (NAD83 UTM Zone 15N)
PROJECTION = "4326"

# Coverage: "point" (centroids) or "polygon" (grid cells)
COVERAGE = "point"

# Set to a path for a county shapefile if you want county-level aggregation.
# Download from: https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html
# e.g. tl_2023_19_county.zip (FIPS 19 = Iowa)
IOWA_COUNTIES_SHP = None  # e.g. "data/tl_2023_19_county/tl_2023_19_county.shp"

RAW_DIR  = Path("data/raw")
OUT_DIR  = Path("data")
RAW_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

# IEM shapefile URL template
SHP_URL = (
    "https://mesonet.agron.iastate.edu/rainfall/dshape.php?"
    "month={month}&day={day}&year={year}"
    "&geometry={coverage}"
    "&duration=day"
    "&epsg={projection}"
)

# ── Step 1: Download daily shapefiles ─────────────────────────────────────────

def date_range(start: date, end: date):
    """Yield every date from start to end inclusive."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)

def download_shapefile(d: date, raw_dir: Path) -> Path | None:

    fname = raw_dir / f"iowa_rain_{d.isoformat()}.zip"

    if fname.exists():
        return fname

    url = SHP_URL.format(
        month=d.month,
        day=d.day,
        year=d.year,
        coverage=COVERAGE,
        projection=PROJECTION
    )

    print(f"  [DEBUG] Requesting: {url}")

    try:
        resp = requests.get(url, timeout=(10, 30))

        print(
            f"  [DEBUG] Status: {resp.status_code}, "
            f"Content-Type: {resp.headers.get('Content-Type')}, "
            f"Bytes: {len(resp.content)}"
        )

        resp.raise_for_status()

        if not resp.content.startswith(b"PK"):
            print(f"  [WARN] Not a zip response: {resp.content[:100]}")
            return None

        fname.write_bytes(resp.content)
        return fname

    except requests.exceptions.Timeout:
        print(f"  [TIMEOUT] {d}")
        return None

    except Exception as e:
        print(f"  [ERROR] {d}: {e}")
        return None
        

# ── Step 2: Parse a single shapefile zip into a DataFrame ─────────────────────

def parse_shapefile_zip(zip_path: Path, d: date) -> pd.DataFrame | None:
    """
    Open a zipped shapefile and return a DataFrame with columns:
        date, lon, lat, precip_in, geometry (if polygon)
    """
    try:
        with zipfile.ZipFile(zip_path) as zf:
            # geopandas can read directly from a zip via a vsiz path,
            # but it's safer to extract to a BytesIO-based VFS.
            gdf = gpd.read_file(f"/vsizip/{zip_path}")
    except Exception as e:
        print(f"  [ERROR] parsing {zip_path.name}: {e}")
        return None

    if gdf is None or gdf.empty:
        return None

    # Normalize column names (IEM uses 'data_in' or similar)
    gdf.columns = [c.lower() for c in gdf.columns]

    # Identify the precipitation column (usually 'data_in' or 'rainfall')
    precip_col = next(
        (c for c in gdf.columns if any(k in c for k in ["data", "rain", "precip"])),
        None
    )
    if precip_col is None:
        print(f"  [WARN] No precip column found in {zip_path.name}. Columns: {list(gdf.columns)}")
        return None

    gdf = gdf.rename(columns={precip_col: "precip_in"})
    gdf["date"] = d

    # For point coverage: extract lon/lat from geometry
    if COVERAGE == "point":
        gdf["lon"] = gdf.geometry.x
        gdf["lat"] = gdf.geometry.y
        return pd.DataFrame(gdf[["date", "lon", "lat", "precip_in"]])
    else:
        # Keep geometry for polygon coverage (useful for spatial joins)
        return gdf[["date", "precip_in", "geometry"]]


# ── Step 3: Stack all days into one time series DataFrame ─────────────────────

def build_time_series(start: date, end: date) -> gpd.GeoDataFrame | pd.DataFrame:
    dates = list(date_range(start, end))
    frames = []

    print(f"\nDownloading & parsing {len(dates)} daily shapefiles ({start} → {end})...")
    for d in tqdm(dates):
        zip_path = download_shapefile(d, RAW_DIR)
        if zip_path is None:
            continue
        df = parse_shapefile_zip(zip_path, d)
        if df is not None:
            frames.append(df)

    if not frames:
        raise RuntimeError("No data was successfully loaded.")

    combined = pd.concat(frames, ignore_index=True)
    combined["date"] = pd.to_datetime(combined["date"])
    combined = combined.sort_values(["date", "lat", "lon"]).reset_index(drop=True)

    print(f"\nLoaded {len(combined):,} point-day records.")
    print(combined.dtypes)
    print(combined.head())
    return combined


# ── Step 4 (optional): Aggregate to county level ──────────────────────────────

def aggregate_to_county(df: pd.DataFrame, county_shp: str) -> pd.DataFrame:
    """
    Spatial join rain points to Iowa counties, then average precip per county per day.
    Requires a county shapefile (e.g. from Census TIGER).
    """
    print("\nAggregating to county level...")
    counties = gpd.read_file(county_shp)[["GEOID", "NAME", "geometry"]]
    counties = counties.to_crs("EPSG:4326")

    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["lon"], df["lat"]),
        crs="EPSG:4326"
    )

    joined = gpd.sjoin(gdf, counties, how="left", predicate="within")
    county_daily = (
        joined.groupby(["date", "GEOID", "NAME"])["precip_in"]
        .mean()
        .reset_index()
        .rename(columns={"NAME": "county", "GEOID": "fips"})
    )
    county_daily["date"] = pd.to_datetime(county_daily["date"])
    return county_daily


# ── Step 5: Feature engineering helpers for ML ────────────────────────────────

def add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add rolling windows and calendar features useful for prediction."""
    df = df.sort_values(["lon", "lat", "date"])  # or ["fips", "date"] for county

    group_cols = ["lon", "lat"] if "lon" in df.columns else ["fips"]

    # Rolling precipitation sums (useful predictors for flood/drought models)
    for window in [3, 7, 14, 30]:
        df[f"precip_{window}d_sum"] = (
            df.groupby(group_cols)["precip_in"]
            .transform(lambda x: x.rolling(window, min_periods=1).sum())
        )

    # Calendar features
    df["year"]       = df["date"].dt.year
    df["month"]      = df["date"].dt.month
    df["day_of_year"] = df["date"].dt.dayofyear
    df["week"]       = df["date"].dt.isocalendar().week.astype(int)

    return df


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    # Build the raw daily time series
    df = build_time_series(START_DATE, END_DATE)

    # Optional county aggregation
    if IOWA_COUNTIES_SHP and os.path.exists(IOWA_COUNTIES_SHP):
        df_county = aggregate_to_county(df, IOWA_COUNTIES_SHP)
        df_county = add_time_features(df_county)
        out_county = OUT_DIR / "iowa_rainfall_county.parquet"
        df_county.to_parquet(out_county, index=False)
        print(f"\nSaved county-level data → {out_county}")

    # Add ML-ready features to point data
    df = add_time_features(df)

    # Save to Parquet (efficient columnar format, ~10x smaller than CSV)
    out_path = OUT_DIR / "iowa_rainfall_daily.parquet"
    df.to_parquet(out_path, index=False)
    print(f"\nSaved point-level data → {out_path}")
    print(f"Shape: {df.shape}")
    print(df.describe())