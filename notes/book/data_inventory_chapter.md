# Data Inventory

This chapter is a complete accounting of every external data source the project consumes: what each one is, who publishes it, what we take from it, in what form and at what grain, how it is acquired, and what is strange about it. Sources are grouped by their role in the data pipeline (a later chapter).

Every source lands in one of three acquisition classes, developed at the end of the chapter: **API archives** (pulled programmatically, snapshot-manifested, recoverable by re-fetch), **bulk archives** (one-time large downloads kept whole), and **vendored files** (obtained manually — a browser click or an emailed export — and kept in the tree as the authority). The class answers the most practical inventory question: what happens if we lose this?

## The master table

One row per source. The window column gives the span the build actually requests, not the span the source offers; the collection window opens 2008-01-01 (`parameters/scope.toml`) and has no upper bound.

| source | provider | role | form | grain | window used | class | consumed by |
|---|---|---|---|---|---|---|---|
| USGS water data | USGS | water records | point time series | native (IV) + daily (DV) | 2008– | API | a2/a3 → d2 |
| IWQIS | IIHR, U. Iowa | water records | point time series | ~15 min | 2008– | vendored | a2/a3 → d2 |
| MPCA CSG | Minnesota PCA | water records | point time series | minute-grain | 2008– | vendored | a2/a3 → d2 |
| NHDPlus V2 | USGS/EPA | hydrography | lines, polygons, tables | medium-res (1:100k) | static | bulk | a3 → everything |
| NLDI | USGS | hydrography | basin polygons | per query | static | API | a1; basin editor |
| HydroSHEDS DIR | HydroSHEDS (WWF et al.) | hydrography | raster | 15 arc-sec | static | bulk | access d8 machinery |
| gridMET | Climatology Lab, U. Idaho | weather | raster time series | 4 km, daily | 2008– | API | a3 → d5 weights |
| CDL | USDA NASS | land surface | raster | 30 m, annual | water-record span | bulk | d5 crops |
| gTREND-Nitrogen | Chang et al. (PSU/Waterloo) | land surface | raster | 250 m, annual | 2000–2017 | vendored | d5 nutrients |
| AgTile | AgTile-US | land surface | raster | 30 m, static | static | bulk | d5 agtile |
| StreamCat | EPA | attributes | tabular by COMID | catchment | static | API | a3 attributes |
| NID | USACE | structures | points + attributes | per dam | current | API | d5 structures |
| ICIS-NPDES outfalls | EPA ECHO | structures | points | per outfall | current | API | a3 → d5 structures |
| DMR loadings | EPA ECHO | structures | tabular | annual, per permit | FY window | API | a3 → d5 structures |
| CWNS 2022 | EPA | structures | relational tables | per facility | 2022 survey | vendored | d5 structures |
| NWM retrospective | NOAA | auxiliary | reach time series | hourly, by COMID | 1979-02–2023-01 | API | acquired only |
| HydroWASTE v1.0 | Ehalt Macedo et al. | reference | points | per plant | static | vendored | cross-check only |

## Water records

The three sensor networks that supply the observations themselves. All three are read through source adapters with one uniform signature, and all three registered cohorts together comprise the census: 20,233 registrations — 20,045 USGS, 156 IWQIS, 32 MPCA. The registry recognises 16 channels across them: nitrate, discharge, gage height, water temperature, specific conductance, three turbidity variants (FNU, NTU, unknown-unit), dissolved oxygen, pH, fDOM, chlorophyll and phycocyanin fluorescence, orthophosphate, and two canary channels (groundwater level, precipitation) carried for drift detection rather than modeling.

### USGS

*what it is:* The federal monitoring network, reached through two services — the modern waterdata OGC API (`api.waterdata.usgs.gov`: monitoring-locations, time-series metadata, and continuous/instantaneous collections) and the legacy NWIS daily-values service.

*what we take:* Station registrations over the AOE, per-series declared metadata (parameter, begin, end), and observations on every registry channel a station declares. The fetch is two-tiered by declaration: stations declaring only cheap channels get a daily-values pull; stations declaring nitrate get the full native-resolution pull, windowed because the continuous API refuses envelopes wider than ~1100 days.

*form and grain:* Point time series. Native (typically 15-minute) resolution on the continuous tier, pre-aggregated daily statistics on the DV tier — a DV day carries a mean and nothing else, a fact the canonicalisation stage must respect (no fabricated spread).

*window used:* 2008-01-01 onward, clipped per-series to the API's own declared active window — asking for years a series declares empty costs a round trip exactly like a full one.

*route and landing:* API class. Metadata and observations land as per-sensor native parquets under `acquired/water/native/`; the raw GeoJSON of every monitoring-locations response is archived under `acquired/metadata/usgs_geojson/`, because the JSON text is the only carrier of declared coordinate precision (decimal places survive in text and die in floats). Requests run under an API-key rotation ring with status-aware backoff.

*quirks:* The API answers a malformed id with HTTP 400 for the whole batch, so metadata requests are bisected on failure. Declared spans can lie in both directions — the span screen treats a declared end before the window's open as disqualifying, and nothing else. Sentinel convention: −999999 in native units. One station in the export (`WQS0127`-class cases aside — that one is IWQIS) can hold data the registry cannot see; the a4 reconciliation names such rows rather than dropping them.

*citation:* USGS Water Data for the Nation; NWIS daily values.

*sample:* the native parquet of `USGS:05465500` (Iowa River at Wapello) across a midnight boundary — columns are pcodes exactly as the API names them: `99133` nitrate and `00065` gage height at 15-minute cadence, and the `_dv` daily-values tier landing once per day (`00060_dv`, discharge, 27,500 cfs).

| datetime | 99133 | 00065 | 00010_dv | 00060_dv |
|---|---|---|---|---|
| 2019-06-10 23:30:00+00:00 | 6.22 | 19.7 |  |  |
| 2019-06-10 23:45:00+00:00 | 6.22 | 19.7 |  |  |
| 2019-06-11 00:00:00+00:00 | 6.22 | 19.7 |  | 27,500 |
| 2019-06-11 00:15:00+00:00 | 6.23 | 19.7 |  |  |
| 2019-06-11 00:30:00+00:00 | 6.22 | 19.7 |  |  |

### IWQIS

*what it is:* The Iowa Water Quality Information System, IIHR's real-time sensor network. The project holds it as a vendored export: an 87-file, 43,430,699-row CSV drop (~3.4 GB) plus a station registry (`sites.csv`), both prepared once by tracked scripts and never altered in place.

*what we take:* All 24 export columns for every station in the `WQS`/`WQP` namespaces — nitrate and the standard channels, plus instrument-validity signals (`r_exc`/`m_exc`, reference and measurement extinction from the optical nitrate sensor) that the previous build discarded. 156 registrations: 149 with coordinates, 7 carrying the coordinate sentinel.

*form and grain:* Point time series at roughly 15-minute cadence, with genuine DST-aware Central-time offsets in the source text — the stored parquets are a real Central-to-UTC conversion, not a relabel.

*window used:* 2008-01-01 onward; the export is the authority for what exists.

*route and landing:* Vendored class. The export is split once per build into per-station parquets under `acquired/water/iwqis/`; a new IIHR export is a re-run of two preparation scripts, not a code change.

*quirks:* The registry and the export disagree, and each is authoritative for a different thing — the export for observations, the registry for location; stations falling between them (`WQP0016` historically, `WQS0127` currently) are reported and ledger-acknowledged, never silently dropped. The coordinate sentinel is −99.0: seven stations are registered with null geometry rather than a false position. The `WQP` conservation-practice namespace inverts the WQS naming convention (`river` holds the watercourse, `nickname` holds what the station is). The `WQT` namespace was declined by decision — its single member is a paired-installation demo carrying 112,603 rows and no values. The export ships `params.csv` and `measures.csv` declaring units, instruments, and series spans; the pipeline deliberately reads neither, and this inventory is where that fact is on record.

*citation:* IWQIS, IIHR — Hydroscience & Engineering, University of Iowa.

*sample:* `WQS0031` — nitrate at ~15-minute cadence beside the optical sensor's own extinction diagnostics (`r_exc`/`m_exc`), the instrument-validity channels the previous build discarded.

| datetime | nitrate_con | r_exc | m_exc | temp_water | turbi_mean |
|---|---|---|---|---|---|
| 2015-12-01 18:27:54+00:00 | 11.2 | 0.387 | 1.4 |  |  |
| 2015-12-01 18:45:12+00:00 | 11.2 | 0.389 | 1.4 |  |  |
| 2015-12-01 19:00:04+00:00 | 11.1 | 0.388 | 1.4 |  |  |
| 2015-12-01 19:15:10+00:00 | 11.1 | 0.39 | 1.4 |  |  |

### MPCA

*what it is:* The Minnesota Pollution Control Agency's cooperative stream gaging (CSG) program, held as vendored per-station zip exports (`csg_{id}.zip` under `raw/water/MPCA_CSG/`) plus a site list.

*what we take:* 32 stations' full records on the channels the registry maps — nitrate, discharge, temperature, turbidity among them.

*form and grain:* Point time series at minute grain — the densest records in the census; a single station's turbidity alone contributes ~145,000 channel-cells.

*window used:* 2008-01-01 onward.

*route and landing:* Vendored class. Natives are materialised from the zips by the records stage; a fresh MPCA delivery is a file drop plus a forced re-materialisation.

*quirks:* Three sentinel conventions coexist in the exports — −999999, −99999, and 999.99 — and the third is the treacherous one, sitting inside plausible physical range for some channels; all three are declared per-source in the registry and scrubbed at the cell grain with counts kept (the measured ground truth: station 41043001 carries 49,531 turbidity sentinels and 14,631 nitrate sentinels). Coordinate precision comes from the export text, read with string dtypes so trailing zeros survive.

*citation:* Minnesota Pollution Control Agency, cooperative stream gaging data.

*sample:* `41043001` — the literal 999.99 turbidity sentinel sitting mid-record beside live nitrate and discharge, exactly the in-range fill value the registry scrub exists for.

| datetime | nitrate | turbidity | discharge | water_temp |
|---|---|---|---|---|
| 2025-07-23 18:15:00+00:00 | 5.76 | 999.99 | 810 |  |
| 2025-07-23 18:30:00+00:00 | 5.78 | 999.99 | 810 |  |
| 2025-07-23 18:45:00+00:00 | 5.77 | 999.99 | 810 |  |
| 2025-07-23 19:00:00+00:00 | 5.76 | 999.99 | 810 |  |

## Hydrography and network

### NHDPlus V2

*what it is:* The medium-resolution (1:100k) national hydrography dataset — the flowline network, its value-added attribute table (VAA), the catchment polygons in one-to-one correspondence with reaches, and the waterbody polygons — distributed as the ~7.3 GB national seamless geodatabase (`nhdplus_l48`).

*what we take:* Four layers clipped to the AOE: `NHDFlowline_Network` (1,511,095 reaches), the VAA (same key set), `CatchmentSP` (1,485,343 polygons), and `NHDWaterbody` (the full layer, not a by-id subset — a graph-builder asking whether a flow path crosses a reservoir needs polygons for reaches no sensor sits on). The COMID is the project's universal spatial key: sensors snap to it, covariates aggregate over it, NWM inherits it.

*form and grain:* Vector lines, polygons, and attribute tables; static.

*route and landing:* Bulk class. One national download, layers extracted and clipped once, landing as parquets under `acquired/network/`. Published as-provided — the pipeline adds nothing to them.

*quirks:* 2.14% of reaches have no catchment and 6,659 catchments have no reach — the layer's own semantics, recorded so absence reads as expected rather than as loss. A `WBAREACOMI` may reference an `NHDArea` wide-river polygon rather than an `NHDWaterbody`, and then resolves to nothing by design. Coastline reaches are non-drainage and excluded from accumulation.

*citation:* NHDPlus Version 2 (USGS/EPA); McKay et al., NHDPlus Version 2 User Guide.

*sample:* a 0.9°x0.6° window at Old Mans Creek, IA — 2,341 flowlines (width by stream order), 2,309 catchment boundaries in gray, 173 waterbodies including the Iowa River's Coralville reservoir; the red triangle is gauge USGS 05455100.

![NHDPlus sample](images/sample_nhdplus.png)

### NLDI

*what it is:* The USGS Network-Linked Data Index, a web service answering network navigation and basin delineation queries keyed on the NHDPlus network.

*what we take:* Upstream basin polygons for the seed sensors — the basins whose union with the seed boxes defines the AOE. The basin editor also makes a sanctioned live NLDI call as its `basin0` reference method.

*form and grain:* One polygon per query; static.

*route and landing:* API class; seed basins land under `acquired/seed_basins/`.

*quirks:* Returns at least one self-intersecting polygon (repaired before union, a hard requirement rather than tidiness), and has returned a 5.06M km² basin for a Cape Cod site snapped onto the Mississippi — delineations above the physical ceiling of CONUS drainage (~2.97M km² at Natchez) are flagged and excluded from the extent union.

*citation:* USGS Hydro Network-Linked Data Index.

*sample:* the delineated upstream basin for seed `IWQIS:WQS0005` (English River), 1,488 km² — one API answer, boundary as returned.

![NLDI sample](images/sample_nldi.png)

### HydroSHEDS drainage direction

*what it is:* The HydroSHEDS v1 15 arc-second Drainage Direction (DIR) raster for North America — a D8 flow-direction grid derived from conditioned SRTM elevation.

*what we take:* The DIR raster clipped to the covariate bounding box (`flowdir_15s.tif`), the substrate for the access layer's D8 flood-fill basin machinery (the basin editor's `basin3`).

*form and grain:* Raster, 15 arc-sec (~450 m); static.

*route and landing:* Bulk class, manual download from hydrosheds.org, staged under `raw/basins/cache/`.

*quirks:* It replaced a legacy 500 m Iowa-only PNG flow grid; the machinery falls back to the legacy raster when the HydroSHEDS clip is absent, so the presence of the new raster — not the code path — decides which algorithm a basin came from.

*citation:* Lehner, Verdin & Jarvis (2008), HydroSHEDS.

*sample:* the D8 direction codes over the same window as the NHDPlus figure — each ~450 m cell drains to one of eight neighbors, and the flood-fill basin machinery walks this field upstream.

![HydroSHEDS sample](images/sample_hydrosheds.png)

## Weather

### gridMET

*what it is:* Daily ~4 km (1/24°) gridded surface meteorology for CONUS, 1979–present, from the Climatology Lab at the University of Idaho.

*what we take:* Eleven variables — precipitation, min/max temperature, min/max relative humidity, specific humidity, wind speed, shortwave radiation, vapor pressure deficit, and both reference evapotranspirations (grass and alfalfa) — over the AOE, tiled rather than pulled as one bounding box (the AOE's bounding box is 8.4M km² against a 4.28M km² polygon; more than half of an untiled transfer would be masked away on arrival).

*form and grain:* Raster time series, 4 km daily, float32 (the source's precision does not justify eight bytes).

*window used:* 2008-01-01 onward, accumulated quarterly.

*route and landing:* API class, but through the NCSS endpoint directly (`thredds.northwestknowledge.net`) rather than through `pygridmet` — the library retries any response containing NaN on the assumption of corruption, and gridMET is CONUS-land-only, so NaN is the *correct* value over the Great Lakes and the ocean; every box we ask for contains water. Landing as quarterly parquets under `acquired/weather/`.

*quirks:* The NaN-over-water behavior above is the load-bearing one. The grid is fixed, so catchment weights against it are computed once in d5 and closed under global cell conservation.

*citation:* Abatzoglou (2013), gridMET.

*sample:* one day of one variable — precipitation on 2008-06-30 over the AOE's 231,080 cells; the faint rectangular seams are the acquisition tiles, and the AOE silhouette is the polygon the tiling exists to respect.

![gridMET sample](images/sample_gridmet.png)

## Land-surface covariates

### Cropland Data Layer

*what it is:* USDA NASS's annual 30 m land-cover classification of CONUS, crop-specific.

*what we take:* Every national annual raster (`{year}_30m_cdls.tif`) within the water record's span, reduced per catchment to the long format `(comid, year, code, frac, n_px)` with coverage kept beside it. No class collapse happens at build time — the 100+ native codes survive to the access layer, where a remap is an explicit reader choice.

*form and grain:* Raster, 30 m, annual.

*window used:* The water record's span; CDL's national coverage begins 2008, which coincides with the collection window's open.

*route and landing:* Bulk class; the national zips under `raw/crops/`, reduced in d5.

*quirks:* Fractional zonal reduction — a boundary pixel contributes its area share to every catchment it touches — and the per-catchment code fractions must sum to that catchment's coverage, which is a registered closure check, not an assumption.

*citation:* USDA National Agricultural Statistics Service, Cropland Data Layer.

*sample:* a 24 km window at Old Mans Creek in NASS's own embedded palette — corn (yellow) and soybean (green) checkerboard against Iowa City's gray urban mass and the Iowa River corridor; this window is 25% corn, 20% soybeans.

![CDL sample](images/sample_cdl.png)

### Nitrogen surplus and fertilizer input

*what it is:* Two components of **gTREND-Nitrogen** (v1.0, March 2026) — a 250 m, EPSG:5070, annual nitrogen mass-balance dataset for CONUS built by downscaling the county-scale TREND-Nitrogen series with gridded land-use and population data. The full dataset carries 22 components over 1930–2017; we hold the N surplus (`Surplus_N_{year}.tif`) and farm fertilizer (`Fertilizer_Ag_{year}.tif`) layers, in the dataset's own units of kg-N ha⁻¹ yr⁻¹.

*what we take:* Both series, reduced per catchment in d5's nutrients product (kg and kg/ha forms, at 6.25 ha per pixel).

*form and grain:* Raster, 250 m, annual.

*window used:* Our archive holds 2000–2017 of the dataset's 1930–2017; 2017 is the dataset's end, not a truncated download. Joined to the water record on year downstream.

*route and landing:* Vendored class (a figshare download), staged under `raw/surplus/national/` and `raw/fertilizer/national/`; the dataset README is kept at `reference/data-source-readmes/gTREND-Nitrogen_README.pdf`.

*quirks:* The surplus is a full mass balance, not fertilizer-minus-uptake — it sums fertilizer (farm and non-farm), fixation, atmospheric deposition, manure, and human waste, and subtracts crop and pasture uptake, with wastewater treatment deliberately unmodeled (human N counts as landscape-retained). The downscaling assigns each county's per-hectare rate uniformly across its agricultural cells via a binary land-use mask, so within-county variation in the raster is land-use pattern, not measured application variance — the authors state it is a watershed-scale product, not a field-scale one, which is exactly the grain we consume it at. Legacy per-tile surplus parquets from the previous build sit beside the national rasters and are consumed by nothing.

*citation:* Chang, Byrnes, Basu & Van Meter (2026), gTREND-Nitrogen — long-term nitrogen mass balance data for the contiguous United States (1930–2017), Scientific Data, doi:10.1038/s41597-026-06576-x; data doi:10.6084/m9.figshare.29897375.

*sample:* the 2017 national surplus raster, downsampled 12x — the corn belt's hotspot band reads directly off the source.

![N surplus sample](images/sample_surplus.png)

### AgTile

*what it is:* A 30 m map of tile-drained cropland for CONUS (AgTile-US), a static geospatial-model product.

*what we take:* The national raster, reduced per catchment to a tile-drained fraction in d5.

*form and grain:* Raster, 30 m, static.

*route and landing:* Bulk class, under `raw/agtile/national/`.

*quirks:* Tile drainage is arguably the single most nitrate-relevant land-surface attribute in the corn belt, and it is a modeled product, not a survey — treat its fractions as a covariate with its own error, not as ground truth.

*citation:* Valayamkunnath et al. (2020), AgTile-US.

*sample:* the clipped archive over the upper Midwest, downsampled 8x — the dense diagonal band is the tile-drained Des Moines lobe.

![AgTile sample](images/sample_agtile.png)

## Catchment attributes

### StreamCat

*what it is:* EPA's catchment-attribute compendium for the NHDPlus V2 network — hundreds of physiographic, climatic, soil, and anthropogenic metrics precomputed per COMID at catchment and watershed scope.

*what we take:* The latest year of every metric family at catchment scope, excluding metrics fitted on water-quality targets (keeping those would leak a modeled answer into the features).

*form and grain:* Tabular by COMID; static (per-family latest year).

*route and landing:* API class — `api.epa.gov/StreamCat` under the api.data.gov umbrella, which returns rate-limit headers on every response, so the remaining allowance is readable rather than inferred from failures. Landing under `acquired/attributes/`.

*quirks:* Every one of the 1,146 attributes whose description declares a sentinel declares −9999 and nothing else, so a blanket scrub is safe *here* — and unsafe in general: `CAT_TILES_Early90s` encodes NODATA as 100, which no description states. The catalogue's year metadata is broken upstream and patched at acquisition.

*citation:* Hill et al. (2016), The Stream-Catchment (StreamCat) Dataset.

*sample:* four of the ~1,146 fetched metrics for four catchments — agricultural drainage percentage, nitrogen surplus, lithological N, high-slope imperviousness.

| comid | pctagdrainagecat | nsurpcat | rockncat | pctimpslphigh2019cat |
|---|---|---|---|---|
| 10944 | 0 | 886 | 23.6 | 0.09 |
| 10946 | 0 | 1.62e+03 | 31.1 | 0.49 |
| 10948 | 0 | 1.72e+03 | 43.8 | 0 |
| 10950 | 0 | 2.91e+03 | 75.6 | 0.13 |

## Structures

### National Inventory of Dams

*what it is:* The US Army Corps of Engineers' registry of dams, with location, size, purpose, and completion year.

*what we take:* The national geopackage over the AOE; dams become a snapped point layer `(comid, snap_pos_m, snap_dist_m)` on the flowline network in d5, joined to nothing at build time — where a dam sits relative to a sensor is the access layer's question.

*form and grain:* Points with attributes; current registry state.

*route and landing:* API class (`nid.sec.usace.army.mil/api/nation/gpkg`), under `acquired/attributes/`.

*quirks:* Dams cluster on catchment boundaries — they impound the pour points that define them — so catchment membership alone misstates which reach a dam controls; the snapped position is the usable fact.

*citation:* USACE National Inventory of Dams.

*sample:* the four largest impoundments in the AOE by storage (acre-feet) — the Missouri main-stem giants the Missouri arm of the extent drains through.

| name | riverName | yearCompleted | nidHeight | nidStorage | primaryPurposeId |
|---|---|---|---|---|---|
| Soo Locks | St Mary S | 1921 | 17 | 277,540,000 | Navigation |
| Garrison Dam | Missouri River | 1953 | 210 | 26,000,000 | Hydroelectric |
| Garrison Dam - Williston Levee | Missouri River | 1953 | 28 | 26,000,000 | Flood Risk Reduction |
| Oahe Dam | Missouri River | 1966 | 245 | 23,600,000 | Flood Risk Reduction |

### ICIS-NPDES outfalls

*what it is:* EPA ECHO's national layer of permitted point-source outfalls — the locations where NPDES-permitted facilities discharge.

*what we take:* The outfall points over the AOE, snapped to the flowline network in d5 (500 m snap ceiling) and keyed by NPDES permit number — the join key that connects them to loadings and to CWNS attributes.

*form and grain:* Points; refreshed weekly upstream.

*route and landing:* API class (`echo.epa.gov/files/echodownloads/npdes_outfalls_layer.zip`), under `acquired/facilities/`.

*quirks:* One permit fans out to multiple outfalls; the permit, not the point, is the entity everything else joins on.

*citation:* EPA Enforcement and Compliance History Online (ECHO), ICIS-NPDES.

*sample:* the Iowa City outfalls — note the south treatment plant sits 7 km downstream of the Old Mans Creek confluence, inside the window the other figures show.

| npdes_id | facility_name | facility_type | state | lat | lon |
|---|---|---|---|---|---|
| IA0063274 | IOWA CITY REGENCY MOBILE HOME PARK STP | COR | IA | 41.6063 | -91.5355 |
| IA0070866 | IOWA CITY, CITY OF (SOUTH) STP | CTG | IA | 41.6057 | -91.5216 |
| IA0076082 | IOWA CITY WATER TREATMENT PLANT | CTG | IA | 41.6942 | -91.5494 |

### DMR nitrogen loadings

*what it is:* Discharge Monitoring Report data — the self-reported effluent measurements NPDES permittees file — published by ECHO as raw annual archives per fiscal year.

*what we take:* Nitrogen-parameter rows only, filtered from each `npdes_dmrs_fy{year}.zip` at acquisition; the full archives are ephemeral (downloaded, filtered to `dmr_n/fy{year}.parquet`, discarded) because holding every parameter for every permit is ~9 GB per sweep for rows the project will never read.

*form and grain:* Tabular, annual by fiscal year, per permit and outfall.

*window used:* The fiscal years overlapping the collection window.

*route and landing:* API class; the filtered parquets under `acquired/facilities/dmr_n/`.

*quirks:* Self-reported data with all that implies — units and parameter spellings vary by state program, and the ephemeral-filter word list is a declared parameter of the acquisition, not a hidden constant.

*citation:* EPA ECHO, DMR data downloads.

*sample:* fy2010 nitrogen-family rows for one Iowa permit — the parameter and unit vocabulary ("per cfs of streamflow", lb/CFS/d) is the state-program variety the quirks paragraph warns about.

| EXTERNAL_PERMIT_NMBR | PERM_FEATURE_NMBR | PARAMETER_DESC | DMR_VALUE_NMBR | DMR_UNIT_DESC | MONITORING_PERIOD_END_DATE |
|---|---|---|---|---|---|
| IA0003328 | 001 | Nitrogen, ammonia per cfs of streamflow | .146 | lb/CFS/d | 10/31/2009 |
| IA0003328 | 001 | Nitrogen, ammonia per cfs of streamflow | .0706 | lb/CFS/d | 10/31/2009 |
| IA0003328 | 001 | Nitrogen, ammonia per cfs of streamflow | .011 | lb/CFS/d | 11/30/2009 |
| IA0003328 | 001 | Nitrogen, ammonia per cfs of streamflow | .02 | lb/CFS/d | 11/30/2009 |

### Clean Watersheds Needs Survey 2022

*what it is:* EPA's quadrennial census of publicly-owned treatment works (POTWs): treatment level, design flow, population served.

*what we take:* A facility table assembled from the survey's relational tables — `FACILITY_PERMIT` (the NPDES join key), `FACILITIES` (name), `EFFLUENT` (current treatment level), `FLOW` (design flow, MGD, summed per facility), `POPULATION_WASTEWATER` (2022 residential population served) — 17,596 NPDES-permitted facility rows, of which ~13,850 carry treatment level and ~13,820 design flow. Joined onto the snapped outfalls by permit number in d5.

*form and grain:* Relational tables; one survey year (2022).

*route and landing:* Vendored class — the national zip is a manual portal download, kept at `acquired/facilities/vendor/cwns_national.zip`.

*quirks:* The 2022 release is relational where prior releases were flat; nothing about the schema is stable across survey years, and the next CWNS is a re-derivation, not an append.

*citation:* EPA Clean Watersheds Needs Survey 2022.

*sample:* the four largest Iowa POTWs in the assembled table.

| npdes_id | facility_name | treatment_level | design_flow_mgd | population_2022 |
|---|---|---|---|---|
| IA0044130 | DES MOINES METRO WRA WWTP | Advanced | 268 | 748,123 |
| IA0042641 | CEDAR RAPIDS WWTP | Advanced | 56 | 165,738 |
| IA0043052 | DAVENPORT WWTP | Secondary | 52 | 132,801 |
| IA0043095 | SIOUX CITY WWTP | Secondary | 29.6 | 94,220 |

## Auxiliary and reference

### NWM retrospective

*what it is:* NOAA's National Water Model v3.0 retrospective — modeled hourly streamflow for every NHDPlus reach, 1979-02 through 2023-01, as a public zarr store on S3.

*what we take:* Hourly flow for the snapped reaches of record-bearing sensors (the `sensor_comids.txt` parameter file), aggregated and partitioned per COMID. `feature_id` *is* the COMID — NWM inherits NHDPlus's identifiers, so it joins everything else here with no crosswalk.

*form and grain:* Reach time series, hourly, by COMID.

*window used:* The full retrospective; it is a fixed product, so a COMID on disk is final.

*route and landing:* API class (anonymous S3, `noaa-nwm-retrospective-3-0-pds`), partitioned under `acquired/nwm/comid={comid}/`.

*quirks:* **Acquired, consumed by nothing downstream yet.** The retrospective ends in early 2023; recent years need the operational archives, and until that route closes, `published/` promises no NWM columns. This is the inventory's honest asterisk: a store held for a use that is designed but not built.

*citation:* NOAA National Water Model v3.0 retrospective.

*sample:* none on disk yet — the acquired store fills at the first pull, which waits on the D3 sensor-COMID list of the completed build.

### HydroWASTE v1.0

*what it is:* A published global inventory of ~58,500 wastewater treatment plants with locations, population served, and estimated discharge, from the paper's supplementary data.

*what we take:* Nothing into the pipeline. It exists as an independent reference against which our ICIS outfall snapping is cross-checked — HydroWASTE outfalls joined to ours within 1 km, separation distributions reported, disagreements over 500 m audited (`notes/facilities-snapping-report.md`).

*form and grain:* Points; static.

*route and landing:* Vendored class — the CSV is a manual download (the host serves 202s to non-browser clients), kept under `reference/water-treatment-facilities/` beside the source PDFs.

*quirks:* Reference-only by design; promoting it to a pipeline input would circularise the cross-check.

*citation:* Ehalt Macedo et al. (2022), HydroWASTE.

*sample:* the Iowa rows — the Iowa City South plant here is the same facility as ICIS permit IA0070866 two sections up, which is precisely the join the cross-check exercises.

| WASTE_ID | WWTP_NAME | LAT_OUT | LON_OUT | POP_SERVED | WASTE_DIS |
|---|---|---|---|---|---|
| 28761 | IOWA CITY SOUTH WWTP | 41.5350 | -91.5270 | 60780 | 11356.2350 |
| 28762 | IOWA FALLS WWTP | 42.5150 | -93.2480 | 5193 | 10337.9600 |
| 29207 | IOWA GREAT LAKES WWTP | 43.3100 | -95.1770 | 23637 | 7078.7200 |
| 30148 | IOWA SEWERAGE SYSTEM | 30.2480 | -93.1270 | 2588 | 1135.6240 |

## Re-materialisation: what happens if we lose it

The three classes carry three different recovery stories, and the inventory is where each source's story is fixed:

**API archives** (USGS records and metadata, NLDI seed basins, gridMET, StreamCat, NID, ICIS outfalls, DMR, NWM) are recoverable by re-running acquisition — at the cost of time, quota, and the possibility that the source has since revised or retired data. The snapshot mechanism exists precisely because "recoverable in principle" is not "identical": every acquisition pass ends in a manifest cut, so a re-fetch that comes back different is a classified drift event, not a silent substitution.

**Bulk archives** (the NHDPlus national geodatabase, CDL national zips, AgTile, HydroSHEDS DIR) are static products recoverable by re-download, with no revision risk worth naming — but at multi-gigabyte cost, so they are kept whole and adopted across builds rather than re-pulled.

**Vendored files** (the IWQIS export, MPCA zips, CWNS national zip, the nutrient rasters, HydroWASTE CSV) are the irreplaceable class: each was obtained by a human act — a portal session, an emailed export, a browser download a script cannot repeat — and the copy in the tree is the authority. Losing one means asking a person or an agency again, possibly for data they no longer serve in the same form. These are the files backups exist for.

One residue note completes the accounting: `raw/comid_attrs/` holds attribute zips (BFI, contact time, 1992 tile drains) from the previous build, superseded by the StreamCat API and consumed by nothing — they await deletion, not documentation. Source READMEs collected from providers live under `reference/data-source-readmes/`, one per dataset that ships one.

## Not incorporated but noted

- **gTREND-Phosphorus:** [(link)](https://plus.figshare.com/articles/dataset/TREND-Phosphorus_and_gTREND-Phosphorus_-_Long-term_phosphorus_mass_balance_data_for_the_contiguous_United_States_1930-2017_/30158131) is the same thing as the gTREND-Nitrogen Surplus tifs but for phosphorus. There is no one summary tif, it's split across different tifs like Crop_P_Uptake and Farm_P_Fertilizer. Phosphorus is not a focus right now so this is omitted.