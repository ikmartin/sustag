# Data stack

Description of data sources I (Isaac) have checked out to some degree and a description of what I know about accessing them.

## Data sources

Sources with a checkmark are ones I have accessed.

- **NASA POWER** ✅ (https://power.larc.nasa.gov)
  - Daily gridded weather data (temperature, precipitation, humidity, wind speed, solar radiation) for any lat/lon point globally
  - Covers 1981 to near real-time; no authentication required
  - Use case: weather and evapotranspiration inputs for irrigation models; temporal feature engineering

- **USDA NASS QuickStats** ✅ (https://quickstats.nass.usda.gov)
  - County-level crop statistics including yield, harvested area, production volume, and irrigated acreage
  - Annual surveys plus Census of Agriculture (every 5 years); free API key required
  - Use case: target variable (yield) for irrigation optimization models; benchmarking water use by region

- **USDA Cropland Data Layer (CDL)** ✅ (https://nassgeodata.gmu.edu/CropScape)
  - Annual 30m raster of crop types across the continental US, derived from satellite imagery
  - Available from 2008 onward; no authentication required; coordinates must be in CONUS Albers (EPSG:5070)
  - Use case: identifying which fields grow which crops; spatial stratification; combining with ET data at field level

- **OpenET** (https://openetdata.org) ✅
  - Satellite-derived actual evapotranspiration at 30m field-scale resolution across the western US
  - Derived from Landsat imagery; free research account required; currently limited to western US
  - Use case: field-level water consumption estimates; more accurate than reference ET for actual crop water use
  - Access requires a 

- **NASA SMAP** (https://smap.jpl.nasa.gov)
  - Satellite-derived soil moisture at ~9km resolution, updated every 2-3 days
  - Requires free NASA Earthdata account; data in HDF5 format; accessible via `earthaccess` Python library
  - Use case: near-real-time soil moisture input for irrigation scheduling models

- **SSURGO via Soil Data Access (SDA)** ✅ (https://sdmdataaccess.sc.egov.usda.gov)
  - Static soil properties (texture, drainage class, available water capacity, hydraulic conductivity) mapped at 1:24,000 scale. NO TIMESTAMPS.
  - No authentication required; queried via SQL POST requests to SDMDataAccess.sc.egov.usda.gov
  - Use case: static soil covariates for irrigation and yield models; spatial stratification by soil type

- **USGS NWIS** ✅ (https://waterdata.usgs.gov/nwis)
  - Daily streamflow, gauge height, water temperature, and water quality data for thousands of gauging stations across the US
  - No authentication required; accessed via `dataretrieval` Python library
  - Use case: watershed-level water availability and drainage context for regional irrigation analysis

- **USDA LTAR / Ag Data Commons** ✅ (https://agdatacommons.nal.usda.gov)
  - Longitudinal soil, crop, and management data from the USDA Long-Term Agroecosystem Research network
  - Hosted on Figshare; no authentication required for public data; accessed via Figshare API at api.(figshare.com)[https://agdatacommons.nal.usda.gov/search?q=LTAR&itemTypes=3&categories=33725]
  - Use case: timestamped, management-aware soil and yield measurements; the primary source for dynamic soil properties that SSURGO cannot provide

- **3000 Rice Genomes Project (3K RGP)** (https://registry.opendata.aws/3kricegenome) (thanks Erin)
  - SNP genotype data for 3,024 rice varieties from 89 countries, sequenced at ~14x depth
  - Hosted on AWS S3 and NCBI SRA; no authentication required; ~15.4TB total
  - Use case: genotype side of rice GWAS; identifying SNPs associated with water use efficiency, nitrogen use efficiency, and other sustainability traits

- **IRRI SNP-Seek** (https://snp-seek.irri.org)
  - SNP genotype data and basic passport/phenotype data for the 3K rice panel, with analysis tools
  - No authentication required; accessible at snp-seek.irri.org; includes a downloadable 1M SNP GWAS dataset
  - Use case: more convenient access to 3K SNP data than raw SRA; includes some matched phenotype data for drought stress subsets

- **IOWA WQIS** (https://iwqis.iowawis.org/) ✅
  - TEMPORAL Water quality data in Iowa, nitrate, pH, dissolved oxygen concentrations, discharge rates, and temperature among other things.
  - Access granted by Jerry. See it in `/data/IWQIS-data/`.
  - Use case: great timeseries data, can see evolving characteristics. Can extrapolate soil health info from runoff and compare with OpenET.

## Access

- **SSURGO Soil Data**
  - I understand this the best. The file `sda_utils.py` contains some utilities for accessing the data and picking out the stuff that's relevant.
  - See also the notebook `explore-SSURGO.ipynb`.

- **LTAR Data**
  In theory this data should be great, but it isn't one thing. LTAR is a conglomerate of 18 (19 now?) research stations across the US. Not all their data is publicly accessible, and the stuff that is has different access points. Some of it can be downloaded. Best way I've found to access pieces of it so far is by searching "LTAR" at [this link](https://agdatacommons.nal.usda.gov/search?q=LTAR). Here are a couple more links I'm just storing here.
  - [Data access at LTAR](https://ltar.ars.usda.gov/data/data-access/)
  - [Data Inventory](https://ltar.ars.usda.gov/data/data-inventory/)
  - [soilDB](https://ncss-tech.github.io/soilDB/index.html) this is a tool for downloading all kinds of data pertaining to soil (and also water). SSURGO also served by this.
  - [another link for accessing data hopefully](https://ltar.ars.usda.gov/data)
  - Here are links to individual databases. [Variation in patterns of production and water-use efficiency among agroecosystems across the LTAR Network](https://agdatacommons.nal.usda.gov/articles/dataset/Dataset_from_Variation_in_patterns_of_production_and_water-use_efficiency_among_agroecosystems_across_the_LTAR_Network/26863258)

- **All other datasets with checks above:**
  Check out the `explore-more-data.ipynb` notebook. Note the OpenET example requires an access code. I put mine in the notebook (commented out) but there are query limits so please don't run it a bunch.