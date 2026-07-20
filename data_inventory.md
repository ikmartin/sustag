# Data Inventory

## Summary

Two different resolutions: basin level and global grid level.

- Water data (the nitrate_con targets) and basin GeoDataFrame parquets tied to individual site ids. For this reason we refer to "sites" and "basins" interchangably
- Weather, Crop, Surplus data all processed and aggrigated to a single global grid (boundaries given by `bbox_wgs84` in `src/build/pipeline_config.toml`). At runtime, `src.data.access.get_data(site_uid)` grabs relevant data from the global cell ids contained in the basin parquet, so the stitching happens at runtime. This is necessary for the virtual site functionality deployed in the widget.

Data is built (after downloading the raw source, see `README.md`) by `make_data.py`, which in turn calls variou `_make_*.py` scripts. Note this whole pipeline was originally written by hand, but Claude was used to port it to the Erdos specified directory structure  and to decouple the covariate data from the basin architecture. Not all ported files have been manually reviewed in their entirety, but all tests pass and all old functionality is retained.

## Sources
Sources feeding the pipeline. Acquisition lives in `src/build/` (builders + `util/`), raw
snapshots in `src/data/raw/` (gitignored — see download instructions per source). Full
access details (URLs, keys, code) are in the *Access Points* section below.

| Source | What | Access | Builder | License / limits |
|---|---|---|---|---|
| IWQIS | Iowa nitrate sensor time series (`WQS*`) | bulk CSV, by request | `_make_water.py` | By request (IWQIS / U. Iowa IIHR); not an open API |
| USGS-NWIS | USGS nitrate gauges (`USGS-*`) | REST API | `_make_water.py` | public domain |
| NLDI | Drainage-basin delineation (v1/v2) | REST API | `_make_basins.py`, `site_view.py` | public |
| IEM | ~4 km precip Voronoi grid (reference-day geometry) | zip download | `_make_grid.py` | Public (Iowa State U. / IEM); free |
| gridMET | Daily weather (temp, ET, humidity, solar…) | download | `_make_weather.py` | Public domain (Climatology Lab, U. Idaho) |
| USDA CDL | Cropland Data Layer (crop classification raster) | CropScape API | `util/clip_crops.py`, `_make_crops.py` | public |
| N-surplus | Iowa nitrogen-surplus grid (250 m) | static parquet | `util/build_source.py`, `_make_surplus.py` | CC BY 4.0 (gTREND, Nature Sci. Data 2026) |
| NHD | Flowlines & waterbodies (widget overlays) | USGS NHD | `_make_map_overlays.py` | public |

## Access Points

### USGS NWIS (continuous nitrate / water quality — `USGS-*` sites)
- *Description:* Continuous (sub-daily) nitrate concentration + discharge/stage/temperature for USGS monitoring stations; the water target for the USGS-prefixed sites.
- *URL:* https://api.waterdata.usgs.gov/ (accessed via the `dataretrieval` package)
- *Access Method:* `dataretrieval.waterdata` Python client (sets `API_USGS_PAT` env var)
- *Requires API-Key:* **True** (USGS API token, `api-keys.toml → usgs`)

```python
import os
from dataretrieval import waterdata

os.environ["API_USGS_PAT"] = "<your USGS API token>"
df = waterdata.get_timeseries(monitoring_location_id="USGS-05465500", parameter_code="99133")  # nitrate
```

### IWQIS — Iowa Water Quality Information System (nitrate — `WQS*` sites)
- *Description:* High-frequency nitrate sensor data for Iowa Water Quality stations; the water target for the WQS-prefixed sites.
- *URL:* https://iwqis.iowawis.org/
- *Access Method:* Must email someone at the IWQIS and request access.
- *Requires API-Key:* **False**

```python
# Not an API — the build reads the committed chunked CSV export, not a live endpoint:
import pandas as pd
df = pd.read_csv("src/data/raw/water/chunks/iwqis_alldata_chunk1.csv")
```

### USGS NLDI — Network-Linked Data Index (basin delineation, USGS sites)
- *Description:* Upstream drainage-basin polygon for a point, via USGS hydrologic network navigation (used to form the basin1 basins).
- *URL:* https://api.water.usgs.gov/nldi/linked-data
- *Access Method:* HTTP REST (GeoJSON)
- *Requires API-Key:* **False**

```python
import requests
lat, lon = 42.03, -93.62
url = f"https://api.water.usgs.gov/nldi/linked-data/comid/position?coords=POINT({lon} {lat})"
basin = requests.get(f"{url}/navigation/UT/basin", timeout=60).json()
```

### IWQIS basin layers (basin delineation, WQS sites)
- *Description:* Authoritative delineated basin polygons (KMZ) for Iowa WQS stations; used for basin2 when a site is a WQS site.
- *URL:* https://iowawis.org/layers/basins
- *Access Method:* HTTP download of KMZ, parsed to geometry
- *Requires API-Key:* **False**

```python
import requests
kmz = requests.get("https://iwqis.iowawis.org/app/inc/inc_get_object.php?id=<station_id>&subid=0", timeout=30).content
```

### gridMET (additional daily weather data)
- *Description:* Daily gridded weather at 4 km resolution. Includes max/min temp, humidity, VPD, solar radiation, ET, and 1000-hr fuel moisture. Used for all non-precipitation weather features.
- *URL:* https://www.climatologylab.org/gridmet.html (served via the Northwest Knowledge Network THREDDS)
- *Access Method:* `pygridmet` Python package (`get_bygeom`)
- *Requires API-Key:* **False**
- *Built to:* `src/data/interim/weather_global_{year}.parquet` — gridMET + IEM precip interpolated onto the canonical IEM grid, one row per `(date, global_node_id)`. This is a **built (interim)** table, not a raw snapshot; the raw gridMET NetCDF is cached under `src/data/raw/weather/gridMET_raw/`. (This is why the ~4.6 GB download goes in `interim/`, not `raw/`.)

```python
import pygridmet
ds = pygridmet.get_bygeom(
    geometry=(-97.6, 39.8, -89.5, 44.5),           # region bbox
    dates=("2020-01-01", "2020-12-31"),
    variables=["pr", "tmmx", "tmmn", "rmax", "rmin", "vpd", "srad", "pet", "fm1000"],
)
```

### IEM — Iowa Environmental Mesonet (daily precip + the grid)
- *Description:* Daily precipitation shapefiles that define the canonical IEM cell grid (`global_node_id`) and supply the `precip_in_1d` feature.
- *URL:* https://mesonet.agron.iastate.edu/rainfall/dshape.php
- *Access Method:* HTTP download of a daily rainfall shapefile (read with GeoPandas)
- *Requires API-Key:* **False**

### USDA CDL / CropScape (crop land cover)
- *Description:* USDA Cropland Data Layer — annual 30 m crop classification, aggregated to the per-cell crop-fraction features.
- *URL:* https://nassgeodata.gmu.edu/axis2/services/CDLService/GetCDLFile
- *Access Method:* HTTP GET → XML with a GeoTIFF URL → stream the raster (see `src.build.util.clip_crops`)
- *Requires API-Key:* **False**

```python
import requests, xml.etree.ElementTree as ET
bbox = "126000,1866000,340000,2270000"  # EPSG:5070
r = requests.get(f"https://nassgeodata.gmu.edu/axis2/services/CDLService/GetCDLFile?year=2020&bbox={bbox}", timeout=300)
tiff_url = ET.fromstring(r.content).find(".//{*}returnURL").text
tif = requests.get(tiff_url).content
```

### gTREND Surplus Data (Land-borne surplus nitrate data from 2000-2017)
- *Description:* Surplus nitrate data at 250m resolution from a nature paper. Access using instructions from the "Data Records" section of the paper. See also the section in `src/README.md` on Catastrophic data rebuilding.
- *URL:* https://www.nature.com/articles/s41597-026-06576-x
- *Access Method:* Manual download, tifs ingested by `src/data/raw/surplus/tif/`.
- *Requires API-Key:* **False**

### US Census TIGER (state/county boundaries — masking)
- *Description:* Cartographic boundary shapefiles used to mask the region to Iowa when gridding surplus/weather.
- *URL:* https://www2.census.gov/geo/tiger/
- *Access Method:* HTTP download of a shapefile zip (read with GeoPandas)
- *Requires API-Key:* **False**

```python
import geopandas as gpd
states = gpd.read_file("https://www2.census.gov/geo/tiger/GENZ2022/shp/cb_2022_us_state_20m.zip")
```

### Natural Earth (rivers / waterbodies — widget map overlays)
- *Description:* 10 m physical rivers & lake centerlines for the widget's basemap overlays (display only, not a model feature).
- *URL:* https://naciscdn.org/naturalearth/10m/physical/ne_10m_rivers_lake_centerlines.zip
- *Access Method:* HTTP download of a shapefile zip
- *Requires API-Key:* **False**

```python
import geopandas as gpd
rivers = gpd.read_file("https://naciscdn.org/naturalearth/10m/physical/ne_10m_rivers_lake_centerlines.zip")
```

# Data Story

Here's a writeup documenting our thought process as we identified and consolidated our data.

## Target

The two main targets are derived from a collection of 162 water-borne nitrate sensors spread out across Iowa's hydrological network recording nitrate (NO2 + NO3) concentration measurements (mg/L) in 5-15 minute intervals. Each of these sensors began reporting at a different date between 2008 and 2025, and many sensors were decommissioned. There are also significant data gaps in most of the nitrate timeseries in which a sensor was turned off for a time (or malfunctioned and was replaced) before being turned back on again. This means the quality of these timeseries vary significantly from sensor to sensor -- some are mostly nan, some span time periods of 1 year. To standardize and clean the data we filtered out sites that had a NaN reporting rate above 50% or a total lifespan of under 3.92 years (3.92 was chosen because there was one sensor with high quality data we liked a lot). We then manually scanned through the timeseries plots of the remaining sensors and threw away the ones which were clearly garbage (those reporting a perfectly linear nitrate concentration over their entire lifespan, for instance). This left us with 85 sensors.

From these nitrate timeseries, we define two targets: a regression target given by the daily max nitrate value observed by a sensor and a classification target given by the violation category of a sensor. A "violation" is defined by the EPA as a nitrate concentration above 10 mg/L (though in reality levels far below this are still unsafe for human consumption), so in the latter case the target is "1" if the daily maximum nitrate level rose above 10 mg/L and is "0" otherwise.

Sensor data came from two different sources depending on whether the sensors were state or federally operated:
- IWQIS (Iowa Water Quality Information System) sensors: Jerry from the IWQIS gave us a 3.3 GB csv containing the sensor timeseries for 61/85 of our final sites.
- USGS NWIS (National Water Information System) sensors: 24/85 final sensors come from federally operated sites, and were downloaded via API requests.


## Covariates

Water-borne nitrate comes from a variety of sources, but the primary source is agricultural. Many crops, notably corn, require copious nitrogen input into the soil. Excess nitrogen in the soil then leaches into the watershed, largely through precipitation, where some of it flows into the path of a nitrate sensor. Given this, we chose the following as our primary covariates:

- *Crop Distribution*, calculated yearly via satellite imagery from 2000 - 2025. Accessed using the USDA Crops Data Layer to produce GeoTIFFs, where each pixel corresponds to one of 254 different land-use types (Corn, Soybeans, Nonag, etc) at a resolution of 30m x 30m. We refer to this as the *crop data*.
- *Nitrogen Surplus 2000-2017*, a model of yearly average nitrogen surplus in the continental United States from 1930-2017 calculated via the gTREND model in the 2026 Nature Paper [gTREND-Nitrogen - Long-term nitrogen mass balance data for the contiguous United States (1930-2017)](https://www.nature.com/articles/s41597-026-06576-x). One massive GeoTIFF downloaded manually for each year. We refer to this as the *surplus data*.
- *Daily Historical Weather*, precipitation in inches from IEM and then min/max temperature in Celsius, min/max humidity, vapor pressure difference, evapotranspiration in (mm), solar radiation in Joules and 1000h fuel moisture from gridMET. We refer to this as the *weather data*.

Other features we considered but decided against:
- *SSURGO*, detailed static information about soil across the United States. We considered using it as a source of static categorical information for the land surrounding each sensor, but the data was hard to organize and preliminary EDA demonstrated it had little effect on model performance.
- *OpenET Database*, a wonderful dataset with detailed evapotranspiration data. We already had rudimentary evapotranspiration data from gridMET, and felt the difficulty of incorporating this additional datastream outweighed the potential accuracy boost it might provide.
- *Point source polluters*. In addition to agricultural contaminants, there exist discrete point sources of nitrogen pollution, slaughterhouses or pig farms for instance, which provide a roughly constant stream of nitrogen into certain rivers in Iowa. We couldn't find a good single datasource documenting this data in the data-scraping phase of this project, but think the inclusion of these features would provide the single-best model improvement.
- *USDA LTAR*: The Long Term Agricultural Network is a conglomerate of 19 different USDA-affiliated research stations studying various agricultural questions. In theory their data is publicly accessible, in practice, each site provides its own mechanism for searching and accessing their data. There are likely incredibly useful features hiding somewhere here, but we weren't able to find any.
- *NASA SMAP*: This provides soil moisture data updated every 2-3 days at 9km resolution. We didn't bother examining it in EDA. When we began training in earnest we were surprised to find that the inclusion of 1000h fuel moisture, a tag-along fire-hazard indicator in our weather data, measurably improved our models. We reason it is a proxy for long-term hydrology of the soil, which affects the passage of nitrogen from soil to water. While we discovered this too late to incorporate NASA SMAP in our final model, it could be a useful thing to add in a future version.
- *USDA NASS Quickstats*: A massive collection of agricultural data including yield, harvested area, production volume, fertilizer sales and irrigated area. Hard to access, much of it is contained in pdf reports, and potentially of limited usefulness due to its unpredictable collection frequency. An ambitious data scraper could likely find useful categorical labels and fertilizer proxies in here, though.
- *Additional live sensor features*: The sensors themselves reported other features besides nitrate, things like pH, oxygenation level and flow rate. Which particular features were reported varied widely across the various sites, but many of these features could be useful if monitoring locations were filtered to ensure the feature of interest was present in the data for all sites.

## Organization of the Data

Two peculiarities drove our data organization. First, the only pollutants which can possibly contribute to the reading of nitrate sensors deployed in water are those directly upstream of the sensor. This means that the area relevant to a given sensor is the *drainage basin* of the sensor, defined as the set of points directly upstream from the sensor. Second, our crop, surplus and weather data exist on rectangular grids at resolutions of 30m, 250m and 4km respectively. Part of our data cleaning necessarily included, therefore, the calculation of the drainage basins for each sensor and the reconciliation of these three different grids (details on this in the following two subsections). After this process we had, associated to each of our 85 deployed monitoring sites,

- a *basin* parquet, encoding the geometry of the sensor's drainage basin geometry
- a *grid* parquet, data concerning the portion of the weather grid falling inside the sensor's basin, used for joining crop, surplus and weather datasets within a site as well as joining individual basin datasets together
- a *crop* parquet containing all crop data inside a site's basin aggregated to the weather grid
- a *surplus* parquet containing all surplus data inside a site's basin aggregated to the weather grid
- a *weather* parquet containing a daily timeseries of all weather data from the start to end of a sensor's lifetime, buffered on either end by 2 months and aligned to the weather grid natively
- a *water* parquet containing the 5-15 minute frequency nitrate concentration timeseries along with other quantities inconsistently tracked across sites.

### Basin Calculation

Irrelevant/relevant area erroneously included/discluded from a drainage basin could severely limit model performance, hence every other piece of this project was downstream of the drainage basin calculation (get it?). We spent a fair amount of time on this step as we wanted to ensure the ceiling for our model's capabilities was as high as possible. There were three different methods we used to find these drainage basins, and each had its own failure mode:

1. *snap to reference*: use a USGS API to lookup precomputed drainage basins by snapping the GPS coordinates of a sensor to the nearest reference point. This worked well for most sites, but failed horribly for sensors placed on small streams near intersections with major rivers: in the worst cases this led to the drainage basin of a small local sensor incorrectly including all of Montana.
2. *authority lookup*: use the unique identifier of a site (its so called `site_uid`) to lookup the drainage basin directly from either a federal or state authority. This worked quite well for every USGS site but quite badly for the IWQIS sites.
3. *compute the basin algorithmically*: the IWQIS monitoring site provides a feature for displaying a drainage basin for an arbitrary pin drop on the map, and it almost works. It runs client-side in Javascript, so we used Claude to scrape through the site source and reconstruct the algorithm (it uses a GeoTIFF aligned to a reference grid with the direction of flow at every cell rounded to one of the 8 cardinal directions, and then finds the boundary of a basin via depth-first-search).

We then went through and manually selected a basin for each site using one of these three methods, defaulting to 1 and 2 and only resorting to 3 when the other two were nonsensical. After this process there were *still* four sites with basins which included large amounts of downstream area or failed to include the sensor location itself. These four sites were all state operated, so we used either 1 or 3 to calculate basins for pins dropped near the offending locations and then chose those basins which seemed most reasonable and/or most closely matched the one displayed by IWQIS. At the end of this process we doubted the supposed irrefutability of the IWQIS's basins, and think that ours might be marginally more accurate.

The manually chosen preference basins were locked in to a read-only csv and not changed over the course of the project.

### Grid Reconciliation

The weather grid was far larger than either the surplus or crop grids, so the obvious approach here was to snap surplus and crop cells to the nearest weather cell and then aggregate. Unfortunately, the weather grid didn't align to latitude and longitude lines, it was slightly curved, meaning every edge partitioned the cells of the other two grids. This didn't matter for the crop grid (30m/4km resolution ratios), but could have led to nontrivial error in the surplus aggregation.

We first calculated the Voronoi cells for the weather reporting locations. Then we aggregated the surplus data to the weather grid weighted by the area of intersection of surplus grid cells and the weather Voronoi cells, possible using the GeoPandas and Shapely python packages.

The aggregation was easier for crops, we simply summed the total number of pixels of each CDL category in each weather grid cell. To avoid keeping all 254 categories, many of which were redundant (Corn, Sweet Corn, and Pop Corn are all separate categories) we applied an intermediate filter to combine categories. Our final crop category list was "Corn", "Soybeans", "HayPasture" (things like Alfalfa), "Small Grains", "Fallow", "Nonag" (catch all for ~0 Nitrogen contributors) and "Other" for all CDL categories not directly addressed.

At the end of this we had a global weather grid parquet with corresponding grid-aggregated crop and surplus files. We additionally included one grid file per monitoring location containing the portion of the weather grid covering the site's drainage basin. Each of these site-grid files included the fraction of area of each cell contained in the basin as well as an estimate of the distance from the centroid of each cell to the sensor, calculated using an algorithm adapted from the resources in basin-calculation-method (3) above.