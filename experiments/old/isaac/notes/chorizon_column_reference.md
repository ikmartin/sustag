# chorizon Column Reference
## SSURGO 2.3.2 — Source: NRCS Tables and Columns Report, 2014-02-04

Each column exists in three variants where applicable: `_l` (low), `_r` (representative value / RV), `_h` (high). For analysis, `_r` is the standard choice unless you need uncertainty bounds. Units are noted where relevant.

Relevance is assessed against the research question: **does county-level nitrogen surplus predict nitrate drinking water violations, and is this relationship moderated by structural vulnerability in rural counties?** SSURGO covariates serve as soil/hydrological confounders or pathway characterization variables, not as primary predictors.

---

## Identification and Structure

| Column | Label | Description | Relevance |
|--------|-------|-------------|-----------|
| `chkey` | Chorizon Key | Primary key for this horizon row. Used to join to child horizon tables (chaashto, chfrags, etc.). | Required for any join to horizon sub-tables. |
| `cokey` | Component Key | Foreign key linking this horizon to its parent component. Required to roll up to map unit and county level. | Essential — this is the join column for all aggregation. |
| `hzname` | Designation | Horizon designation string (e.g., "Ap", "Bt", "C"). Not a controlled vocabulary in this column; see `desgnmaster` for the parsed master horizon. | Low direct use, but useful for filtering to specific horizon types (e.g., selecting only A horizons for topsoil properties). |
| `desgndisc` | Disc | Discontinuity number (e.g., the "2" in "2Bt"). Indicates a lithologic discontinuity. | Low relevance unless you're characterizing depth to a different parent material. |
| `desgnmaster` | Master | Master horizon designation (A, B, C, O, R, W). Controlled vocabulary. | Moderately useful for horizon-type filtering. |
| `desgnmasterprime` | Prime | Prime symbol modifier on the master designation (e.g., B'). Rare. | Low relevance. |
| `desgnvert` | Sub | Vertical subdivision number within a horizon (e.g., the "1" and "2" in Bt1, Bt2). | Low relevance. |

---

## Depth

| Column | Label | Description | Relevance |
|--------|-------|-------------|-----------|
| `hzdept_l/r/h` | Top depth | Depth to the top of the horizon (cm). | **High.** Required for depth-weighting when aggregating horizon properties to component level. Also needed to compute effective rooting depth and identify the depth interval for leaching calculations. |
| `hzdepb_l/r/h` | Bottom depth | Depth to the bottom of the horizon (cm). | **High.** Same as above. |
| `hzthk_l/r/h` | Thickness | Horizon thickness (cm). Derived from top and bottom depths, stored for convenience. | Useful as an alternative to computing thickness yourself. |

---

## Coarse Fragments (in-profile)

| Column | Label | Description | Relevance |
|--------|-------|-------------|-----------|
| `fraggt10_l/r/h` | Fragments >10 cm | Volume percent of fragments larger than 10 cm. | Low. Iowa soils are predominantly fine-textured; coarse fragments rarely significant. |
| `frag3to10_l/r/h` | Fragments 3–10 cm | Volume percent of fragments 3–10 cm. | Low. Same reasoning. |

---

## Particle Size Distribution (sieve analysis)

| Column | Label | Description | Relevance |
|--------|-------|-------------|-----------|
| `sieveno4_l/r/h` | Sieve No. 4 passing | Percent passing a No. 4 sieve (>4.75 mm retained = gravel). | Low. Engineering classification; minimal relevance for Iowa agricultural soils. |
| `sieveno10_l/r/h` | Sieve No. 10 passing | Percent passing a No. 10 sieve. | Low. Same. |
| `sieveno40_l/r/h` | Sieve No. 40 passing | Percent passing a No. 40 sieve. | Low. Same. |
| `sieveno200_l/r/h` | Sieve No. 200 passing | Percent passing a No. 200 sieve (i.e., silt + clay fraction). | Low. Redundant with `silttotal_r` + `claytotal_r` which are more analytically useful. |

---

## Texture — Sand Fractions (%)

| Column | Label | Description | Relevance |
|--------|-------|-------------|-----------|
| `sandtotal_l/r/h` | Total sand | Total sand content (%). | **Moderate.** Sand content is inversely related to water retention and CEC. High sand = higher leaching potential. Useful as a texture covariate. |
| `sandvc_l/r/h` | Very coarse sand | Very coarse sand fraction (%). | Low. Sub-fraction rarely needed at this analysis scale. |
| `sandco_l/r/h` | Coarse sand | Coarse sand fraction (%). | Low. |
| `sandmed_l/r/h` | Medium sand | Medium sand fraction (%). | Low. |
| `sandfine_l/r/h` | Fine sand | Fine sand fraction (%). | Low. |
| `sandvf_l/r/h` | Very fine sand | Very fine sand fraction (%). | Low. |

---

## Texture — Silt and Clay Fractions (%)

| Column | Label | Description | Relevance |
|--------|-------|-------------|-----------|
| `silttotal_l/r/h` | Total silt | Total silt content (%). | **Moderate.** Iowa soils are silt-dominant (loess parent material). High silt = moderate water retention and leaching. Useful as texture covariate. |
| `siltco_l/r/h` | Coarse silt | Coarse silt fraction (%). | Low. Sub-fraction not needed. |
| `siltfine_l/r/h` | Fine silt | Fine silt fraction (%). | Low. |
| `claytotal_l/r/h` | Total clay | Total clay content (%). | **High.** Clay content strongly affects hydraulic conductivity, water retention, CEC, and denitrification potential. Key texture covariate for N leaching pathway characterization. |
| `claysizedcarb_l/r/h` | Clay-sized carbonates | Clay-sized carbonate content (%). Carbonate particles in the clay size range. | Low. More relevant to calcareous soils in arid regions; minor in most Iowa profiles. |

---

## Organic Matter

| Column | Label | Description | Relevance |
|--------|-------|-------------|-----------|
| `om_l/r/h` | Organic matter | Organic matter content (%). | **High.** Organic matter is a key driver of N mineralization (internal N supply), CEC, and soil structure. Important confounder — high OM soils have greater N cycling capacity and may show different relationships between applied N surplus and leaching. Particularly important for the Mollisol-dominant Iowa profile. |

---

## Bulk Density (g/cm³)

| Column | Label | Description | Relevance |
|--------|-------|-------------|-----------|
| `dbtenthbar_l/r/h` | Bulk density at 0.1 bar | Bulk density measured at 0.1 bar water tension. | **Moderate.** Needed to convert gravimetric water content to volumetric, and to compute total pore space. Useful if computing N mass per volume of soil. |
| `dbthirdbar_l/r/h` | Bulk density at 0.33 bar | Bulk density at field capacity tension. Most commonly used bulk density estimate. | **Moderate.** Same as above; this is the standard value to use. |
| `dbfifteenbar_l/r/h` | Bulk density at 15 bar | Bulk density at permanent wilting point tension. | Low. Rarely needed for N leaching analysis. |
| `dbovendry_l/r/h` | Oven-dry bulk density | Bulk density of oven-dried soil. | Low. Relevant for engineering applications; rarely needed here. |
| `partdensity` | Particle density | Particle density (g/cm³). Used to compute total porosity. | Low direct relevance; occasionally needed for porosity calculations. |

---

## Hydraulic Properties

| Column | Label | Description | Relevance |
|--------|-------|-------------|-----------|
| `ksat_l/r/h` | Saturated hydraulic conductivity | Rate of water movement through saturated soil (µm/s). | **High.** This is the single most important hydraulic property for nitrate leaching potential. High Ksat = rapid water movement through the soil profile = greater leaching to groundwater or tile drains. Should be depth-weighted and aggregated to component level. |
| `awc_l/r/h` | Available water capacity | Volume of water held between field capacity and wilting point (cm/cm). | **High.** AWC determines how much water the soil can hold before draining. Low AWC soils move water (and nitrate) to drainage more quickly. Key covariate. |

---

## Water Retention (%)

| Column | Label | Description | Relevance |
|--------|-------|-------------|-----------|
| `wtenthbar_l/r/h` | Water at 0.1 bar | Gravimetric water content at 0.1 bar (field capacity approximation for coarser soils). | Low for direct use; AWC is more useful. |
| `wthirdbar_l/r/h` | Water at 0.33 bar | Gravimetric water content at 0.33 bar (field capacity for most soils). | Low for direct use. |
| `wfifteenbar_l/r/h` | Water at 15 bar | Gravimetric water content at 15 bar (wilting point). | Low for direct use. |
| `wsatiated_l/r/h` | Water at saturation | Gravimetric water content at saturation (%). | **Moderate.** Satiated water content relates to total pore space and thus the volume of water that can move through the profile. Useful alongside Ksat. |

---

## Soil Behavior (Atterberg limits and related)

| Column | Label | Description | Relevance |
|--------|-------|-------------|-----------|
| `lep_l/r/h` | Linear extensibility percent | Measure of shrink-swell potential (%). Soils with high LEP develop cracks on drying that can create preferential flow paths. | **Moderate.** Preferential flow through shrinkage cracks can dramatically accelerate nitrate leaching, bypassing the soil matrix. Worth including as a pathway variable. |
| `ll_l/r/h` | Liquid limit | Water content at which soil transitions from plastic to liquid behavior (%). Engineering classification. | Low relevance for N transport. |
| `pi_l/r/h` | Plasticity index | Difference between liquid and plastic limits (%). Engineering classification. | Low relevance for N transport. |
| `aashind_l/r/h` | AASHTO group index | Engineering classification index. | Low relevance. |

---

## Erodibility

| Column | Label | Description | Relevance |
|--------|-------|-------------|-----------|
| `kwfact` | Kw erodibility factor | USLE soil erodibility factor based on whole-soil sample. | **Low-moderate.** Erodibility correlates with surface texture and OM. Not directly relevant to nitrate leaching, but may be useful as a proxy for surface runoff N loss pathway vs. leaching pathway. |
| `kffact` | Kf erodibility factor | USLE soil erodibility factor based on fine-earth fraction. | Low-moderate. Same as above. |

---

## Chemistry

| Column | Label | Description | Relevance |
|--------|-------|-------------|-----------|
| `caco3_l/r/h` | Calcium carbonate | CaCO₃ equivalent (%). Measure of free carbonates in the soil. | **Moderate.** Calcareous soils have elevated pH which affects nitrification rates and N availability. Iowa soils often calcareous in subsoil. |
| `gypsum_l/r/h` | Gypsum | Gypsum content (%). | Low. Relevant mainly in arid soils; rarely significant in Iowa. |
| `sar_l/r/h` | Sodium adsorption ratio | Measure of sodium hazard relative to calcium and magnesium. | Low. Relevant for irrigation water quality; rarely significant in humid Iowa. |
| `ec_l/r/h` | Electrical conductivity | Electrical conductivity of the saturation extract (dS/m). Proxy for total dissolved salts. | Low. Salinity not a significant issue in Iowa agricultural soils. |
| `cec7_l/r/h` | CEC at pH 7 | Cation exchange capacity measured at pH 7 (meq/100g). | **High.** CEC is a fundamental measure of soil's ability to retain cations including NH₄⁺. High CEC slows nitrate leaching by retaining ammonium before nitrification. Strong predictor of soil N retention capacity. Correlates with clay and OM content. |
| `ecec_l/r/h` | Effective CEC | Effective CEC = sum of exchangeable cations (meq/100g). Relevant at the soil's actual pH. | **Moderate.** More accurate than CEC7 for acid soils. For Iowa's near-neutral soils, CEC7 and ECEC are similar. |
| `sumbases_l/r/h` | Sum of bases | Sum of exchangeable Ca, Mg, K, Na (meq/100g). | Low direct relevance; base saturation is largely redundant with pH and CEC. |
| `ph1to1h2o_l/r/h` | pH (1:1 water) | Soil pH measured in a 1:1 soil:water suspension. | **High.** pH strongly controls nitrification rates, N transformation pathways, and the speciation of inorganic N. Acid soils suppress nitrification; near-neutral to slightly alkaline soils favor rapid nitrification to NO₃⁻. Key confounder. |
| `ph01mcacl2_l/r/h` | pH (0.01M CaCl₂) | Soil pH measured in CaCl₂ solution. Typically ~0.5 units lower than 1:1 water pH; considered more accurate. | **Moderate.** More precise measure than `ph1to1h2o_r`. Use one or the other, not both. |
| `freeiron_l/r/h` | Free iron | Free iron content (%). Iron oxides affect soil aggregation, phosphorus binding, and reduce conditions relevant to denitrification. | **Moderate.** Free iron is a marker for redox-active soils. In reducing conditions iron is mobilized, creating anaerobic zones where denitrification can occur — relevant to whether nitrate is lost by denitrification before reaching groundwater. |
| `feoxalate_l/r/h` | Oxalate-extractable iron | Amorphous/poorly crystalline iron oxide content (mg/kg). | Low for this analysis. More relevant to phosphorus geochemistry than nitrate transport. |
| `extracid_l/r/h` | Extractable acidity | Exchangeable acidity (meq/100g). Relevant for acid soils. | Low. Iowa soils mostly near-neutral; extractable acidity rarely significant. |
| `extral_l/r/h` | Extractable aluminum | Exchangeable aluminum (meq/100g). Toxic to plants in acid soils. | Low. Same reasoning. |
| `aloxalate_l/r/h` | Oxalate-extractable aluminum | Amorphous aluminum oxide content (mg/kg). | Low. Relevant to P geochemistry, not N leaching. |
| `pbray1_l/r/h` | Bray 1 phosphorus | Available phosphorus by Bray 1 extraction (mg/kg). | Low direct relevance to nitrate, though P status correlates with fertilization intensity — possible proxy for management intensity if needed. |
| `poxalate_l/r/h` | Oxalate-extractable phosphorus | Poorly crystalline P fraction (mg/kg). | Low. Geochemistry context only. |
| `ph2osoluble_l/r/h` | Water-soluble phosphorus | Water-soluble P (mg/kg). | Low direct relevance to nitrate analysis. |
| `ptotal_l/r/h` | Total phosphorus | Total phosphorus content (%). | Low direct relevance to nitrate, though co-loading of N and P is a water quality issue. |

---

## Engineering

| Column | Label | Description | Relevance |
|--------|-------|-------------|-----------|
| `excavdifcl` | Excavation difficulty class | Qualitative rating of how difficult the soil is to excavate. | Low relevance. |
| `excavdifms` | Excavation difficulty moisture | Moisture condition at which excavation difficulty was assessed. | Low relevance. |

---

## Summary: Columns to Pull for Your Pipeline

**Definitely include (depth-weight and aggregate to component via `cokey`):**
- `hzdept_r`, `hzdepb_r` — for depth-weighting
- `claytotal_r` — texture, hydraulic and chemical confounder
- `silttotal_r` — texture covariate (especially relevant in loess-dominated Iowa)
- `sandtotal_r` — texture covariate
- `om_r` — organic matter; N cycling and CEC confounder
- `ksat_r` — saturated hydraulic conductivity; primary leaching pathway variable
- `awc_r` — available water capacity; controls drainage and leaching timing
- `ph1to1h2o_r` — pH; controls nitrification rates
- `cec7_r` — cation exchange capacity; N retention
- `dbthirdbar_r` — bulk density; needed for mass calculations

**Consider including:**
- `lep_r` — shrink-swell / preferential flow indicator
- `wsatiated_r` — saturation water content; total pore space
- `caco3_r` — carbonate content; pH buffer and N transformation context
- `freeiron_r` — redox indicator; denitrification potential proxy
- `ecec_r` — if you want actual-pH CEC rather than pH-7 CEC

**Exclude:**
- Sand sub-fractions (`sandvc`, `sandco`, `sandmed`, `sandfine`, `sandvf`)
- Silt sub-fractions (`siltco`, `siltfine`)
- `claysizedcarb_r` — negligible in Iowa
- Atterberg limits (`ll`, `pi`, `aashind`) — engineering classification, not relevant
- Sieve fractions (`sieveno4` through `sieveno200`) — redundant with texture fractions
- `gypsum_r`, `sar_r`, `ec_r` — salinity/sodicity; not relevant in Iowa
- `feoxalate_r`, `aloxalate_r`, `poxalate_r` — P geochemistry focus
- `extracid_r`, `extral_r` — acid soil chemistry; not significant in Iowa
- `pbray1_r`, `ph2osoluble_r`, `ptotal_r` — phosphorus; low relevance to nitrate model
- Engineering columns (`kwfact`, `kffact`, `excavdifcl`, `excavdifms`) — low priority
- `dbovendry_r`, `dbtenthbar_r`, `dbfifteenbar_r` — `dbthirdbar_r` is sufficient
- Water retention at specific tensions (`wtenthbar`, `wthirdbar`, `wfifteenbar`) — AWC is more useful and integrates these
