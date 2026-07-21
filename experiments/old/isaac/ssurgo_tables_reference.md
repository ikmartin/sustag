# SSURGO 2.3.2 Table Reference

Source: SSURGO Metadata — Tables and Columns Report (NRCS, 2014-02-04)

---

## All Tables

### Horizon Tables (ch*)

**chaashto** — Horizon AASHTO
Contains the American Association of State Highway Transportation Officials classification(s) for the referenced horizon. One row is marked as the representative AASHTO classification.

**chconsistence** — Horizon Consistence
Contains descriptive terms of soil consistence — rupture resistance, plasticity, and stickiness — for the referenced horizon. One row is marked as having representative characteristics.

**chdesgnsuffix** — Horizon Designation Suffix
Contains the designation suffix(es), one per row, for the referenced horizon. For example, the "h" and "s" of a Bhs horizon appear as two rows in this table.

**chfrags** — Horizon Fragments
Lists the mineral and organic fragments that generally occur in the referenced horizon, including volume percent (low/RV/high), fragment kind, size, shape, roundness, and hardness.

**chorizon** — Horizon
Lists the horizon(s) and related data for the referenced map unit component. Contains the core physical and chemical properties: texture fractions (sand, silt, clay), organic matter, bulk density, saturated hydraulic conductivity, available water capacity, water retention at various tensions, pH, CEC, EC, SAR, calcium carbonate, erodibility factors, and excavation difficulty. Horizons with two distinct parts are recorded twice at the same depths.

**chpores** — Horizon Pores
Lists the voids for the referenced horizon, including quantity, size, continuity, and shape. More than one row can be marked RV when a horizon has multiple void sizes or shapes.

**chstruct** — Horizon Structure
Lists the individual soil structure size, grade, and shape terms for the referenced horizon. Terms are assembled into a structure group string recorded in the Horizon Structure Group table.

**chstructgrp** — Horizon Structure Group
Lists the ranges of soil structure for the referenced horizon as concatenated strings. The row with the typically occurring structure is marked as representative.

**chtext** — Horizon Text
Contains notes and narrative descriptions related to the referenced horizon. Often empty for a particular horizon.

**chtexture** — Horizon Texture
Lists the individual texture(s), or terms used in lieu of texture, for the referenced horizon. Only unmodified texture terms; modifiers are in the Horizon Texture Modifier table.

**chtexturegrp** — Horizon Texture Group
Lists the range of textures for the referenced horizon as a concatenation of texture and modifier(s). The RV row identifies the typically occurring texture. Handles stratified textures.

**chtexturemod** — Horizon Texture Modifier
Lists the texture modifier(s) for the referenced texture (e.g., the "gr" in "GR-LS").

**chunified** — Horizon Unified
Contains the Unified Soil Classification(s) for the referenced horizon. One row is marked as the representative Unified classification.

---

### Component Tables (co*)

**cocanopycover** — Component Canopy Cover
Lists the overstory plants that typically occur on the referenced map unit component, with percent canopy cover.

**cocropyld** — Component Crop Yield
Lists commonly grown crops and their expected range in yields (low/RV/high, irrigated and non-irrigated) for the referenced map unit component. Also contains a productivity index and VA soil productivity group. Note: population is highly uneven across states; many states including Iowa have no data in this table.

**codiagfeatures** — Component Diagnostic Features
Lists the typical soil diagnostic features (e.g., ochric epipedon, cambic horizon) for the referenced map unit component, with depth ranges.

**coecoclass** — Component Ecological Classification
Identifies ecological sites typically associated with the referenced map unit component, including NRCS forestland and rangeland ecological sites and USFS Habitat Types.

**coeplants** — Component Existing Plants
Lists rangeland or forestland plants that typically occur on the referenced map unit component, with understory and range production percentages.

**coerosionacc** — Component Erosion Accelerated
Lists the kinds of accelerated erosion that occur on the referenced map unit component. One row is marked as representative.

**coforprod** — Component Forest Productivity
Lists the site index and annual productivity (cubic feet per acre per year, CAMI) of forest overstory tree species that typically occur on the referenced map unit component.

**coforprodo** — Component Forest Productivity — Other
Lists the site index and annual productivity of forest overstory tree species in units other than cubic feet per acre per year.

**cogeomordesc** — Component Geomorphic Description
Lists the geomorphic features on which the referenced map unit component typically occurs.

**cohydriccriteria** — Component Hydric Criteria
Lists the hydric soil criteria met for those map unit components classified as a hydric soil.

**cointerp** — Component Interpretation
Lists the predictions of behavior and limiting features for specified uses made for the referenced map unit component. Contains fuzzy logic interpretation scores (low/RV/high) with class labels.

**comonth** — Component Month
Lists the monthly flooding and ponding characteristics for the referenced map unit component. One row per month of the year. Also contains daily average precipitation and potential evapotranspiration by month.

**component** — Component
Lists the map unit components identified in the referenced map unit and selected properties of each component. Key fields include: component percentage, slope, runoff class, T factor, wind erodibility, erosion class, earth cover kind, hydric condition and rating, drainage class, elevation, aspect, land capability class (irrigated and non-irrigated), crop productivity index, hydrologic group, soil taxonomy (order through subgroup, particle size, CEC activity class, reaction, temperature class, moisture regime), and state-specific fields including Indiana NO3 leaching index and Florida leaching/runoff potential.

**copm** — Component Parent Material
Lists the individual parent material(s) for the referenced map unit component, including kind, origin, and vertical order where soils formed in multiple materials.

**copmgrp** — Component Parent Material Group
Lists the concatenated string of parent material(s) in which the referenced map unit component formed. One row is identified as the representative parent material.

**copwindbreak** — Component Potential Windbreak
Lists the windbreak plant species commonly recommended for the referenced map unit component.

**corestrictions** — Component Restrictions
Lists the root restrictive features or layers for the referenced map unit component, including restriction kind, hardness, and depth range (top and bottom, low/RV/high). Empty if no restrictive features; may have multiple rows if several restrictive features occur. Thickness range determines whether the restriction is present in all or only some delineations.

**cosoilmoist** — Component Soil Moisture
Describes the typical soil moisture profile (depth range and moisture status) for the referenced map unit component during the month referenced in the Component Month table. Twelve months of profiles describe the representative annual situation.

**cosoiltemp** — Component Soil Temperature
Describes the typical soil temperature profile for the referenced map unit component during the month referenced in the Component Month table. Twelve months of profiles describe the representative annual situation.

**cosurffrags** — Component Surface Fragments
Lists organic or mineral fragments that generally occur on the surface of the referenced map unit component, with cover percent and fragment properties.

**cosurfmorphgc** — Component Three Dimensional Surface Morphometry
Lists the typical geomorphic position(s) of the referenced map unit component in three-dimensional terms (mountains, hills, terraces, flats).

**cosurfmorphhpp** — Component Two Dimensional Surface Morphometry
Lists the geomorphic position(s) of the referenced map unit component in two-dimensional hillslope profile terms.

**cosurfmorphmr** — Component Microrelief Surface Morphometry
Lists microrelief features associated with the referenced geomorphic feature.

**cosurfmorphss** — Component Slope Shape Surface Morphometry
Lists the geomorphic shape(s) of the referenced map unit component in slope shape terms (across-slope and up/down-slope).

**cotaxfmmin** — Component Taxonomic Family Mineralogy
Lists the mineralogy characteristics as defined in Soil Taxonomy that apply to the referenced map unit component.

**cotaxmoistcl** — Component Taxonomic Moisture Class
Provides clear identification of the intended taxonomic moisture class as defined in Soil Taxonomy, even though moisture class is implied at a higher taxonomic level.

**cotext** — Component Text
Contains notes and narrative descriptions for the referenced map unit component. Often empty.

**cotreestomng** — Component Trees To Manage
Lists the trees commonly recommended for managing on the referenced map unit component.

**cotxfmother** — Component Taxonomic Family Other Criteria
Lists other taxonomic characteristics (e.g., classes of coatings or permanent cracks) as defined in Soil Taxonomy that apply to the referenced map unit component.

---

### Distribution Metadata Tables (dist*)

**distinterpmd** — Distribution Interp Metadata
Records the set of NASIS fuzzy logic interpretations generated for the map unit components included in a set of distribution data.

**distlegendmd** — Distribution Legend Metadata
Records information about the legends or soil survey areas selected for inclusion in a set of distributed data, including survey status and export certification.

**distmd** — Distribution Metadata
Records information associated with the selection of a set of data for distribution, including the criteria used for selecting map units and components.

---

### Feature Tables (feat*)

**featdesc** — Feature Description
Records the description of all spot features that occur in a soil survey area.

**featline** — Feature Line
Records all spot features of a soil survey area represented as lines (spatial table).

**featpoint** — Feature Point
Records all spot features of a soil survey area represented as points (spatial table).

---

### Legend Tables (l*)

**laoverlap** — Legend Area Overlap
Lists the geographic areas (counties, states, MLRAs, etc.) coincident with the soil survey area identified in the Legend table.

**legend** — Legend
Identifies the soil survey area that the legend is related to, including area symbol, area name, acreage, MLRA office, survey status, project scale, and correlation date.

**legendtext** — Legend Text
Contains notes and narrative descriptions related to the referenced legend. Often empty.

---

### Map Unit Tables (ma*)

**mapunit** — Mapunit
Identifies the map units included in the referenced legend, with map unit symbol, name, kind, status, acreage, farmland classification, HEL class, Iowa CSR, and certification status.

**muaggatt** — Mapunit Aggregated Attribute
Records soil attributes and interpretations aggregated from the component level to a single map unit value. Includes slope gradient, bedrock depth, water table depth, flooding and ponding frequency, available water storage (multiple depth intervals), drainage class, hydrologic group, land capability class, hydric classification presence, and engineering interpretations.

**muaoverlap** — Mapunit Area Overlap
Lists the map units that exist in the overlap between the entire soil survey area and a referenced geographic area in the Legend Area Overlap table.

**mucropyld** — Mapunit Crop Yield
Lists commonly grown crops and their expected yields for the referenced map unit as a whole (as opposed to individual components in cocropyld).

**muline** — Mapunit Line
Records map units represented as lines (spatial table).

**mupoint** — Mapunit Point
Records map units represented as points (spatial table).

**mupolygon** — Mapunit Polygon
Records map units represented as polygons. This is the primary spatial geometry table used for GIS joins.

**mutext** — Mapunit Text
Contains notes and narrative descriptions related to the referenced map unit.

---

### Static Metadata Tables (mdstat*)

**mdstatdomdet** — Domain Detail Static Metadata
Records the individual domain members for all domains associated with the tabular data set. Each record represents one member of a particular domain (e.g., the allowed values for a choice field).

**mdstatdommas** — Domain Master Static Metadata
Records metadata about each domain as a whole. A domain is a fixed set of choices to which a column's value is restricted.

**mdstatidxdet** — Index Detail Static Metadata
Records what columns of a table make up a particular index.

**mdstatidxmas** — Index Master Static Metadata
Records metadata about each index as a whole.

**mdstatrshipdet** — Relationship Detail Static Metadata
Records the pairs of join columns that define a particular table relationship.

**mdstatrshipmas** — Relationship Master Static Metadata
Records metadata about each relationship between tables as a whole, including cardinality.

**mdstattabcols** — Table Column Static Metadata
Records metadata for all columns of all tables, including data types, nullability, min/max values, units, domain names, and column descriptions. The authoritative column-level documentation.

**mdstattabs** — Table Static Metadata
Records metadata about the tables themselves, including physical name, logical name, label, and table description. This is the table that would have answered the original `SELECT * FROM mdstattabs` query.

---

### Miscellaneous Tables

**month** — Month
Lookup table for months of the year (sequence number and name).

---

### Survey Area Tables (sa*)

**sacatalog** — Survey Area Catalog
Records primary dynamic metadata associated with a soil survey area: survey area version, tabular data version, NASIS export date, certification status, and FGDC metadata.

**sainterp** — Survey Area Interpretation
Records information about the soil interpretations generated for a soil survey area, including interpretation name, type, description, and generation date.

**sapolygon** — Survey Area Polygon
Records the polygons making up a soil survey area boundary (spatial table).

---

### Soil Data Viewer Tables (sdv*)

**sdvalgorithm** — SDV Algorithm
Lookup table recording valid algorithms for aggregating soil property values or interpretation results to the map unit level.

**sdvattribute** — SDV Attribute
Each record corresponds to an intrinsic soil property or soil interpretation available in the Soil Data Viewer application (an "SDV rule"). Contains aggregation method, depth qualifiers, month ranges, map legend configuration, and tie-break rules for each mappable attribute.

**sdvfolder** — SDV Folder
Records the folders and subfolders by which soil attributes are grouped and displayed in the Soil Data Viewer application.

**sdvfolderattribute** — SDV Folder Attribute
Associative table resolving the many-to-many relationship between SDV folders and soil attributes.

---

## Tables Relevant to Nitrogen Surplus / Drinking Water Violations Research

The following tables are likely to be useful given the research focus on nitrogen surplus, nitrate drinking water violations, soil hydraulic pathways, and county-level spatial analysis.

---

**component** — The backbone of any SSURGO query. Required to join from map unit to horizon or restriction data, and provides key covariates: drainage class (`drainagecl`), hydrologic group (`hydgrp`), land capability class (`nirrcapcl`/`irrcapcl`), slope, and soil taxonomy. The Indiana nitrate leaching index (`innitrateleachi`) and Florida leaching/runoff potential fields (`flsoilleachpot`, `flsoirunoffpot`) are also here and may be useful if their methodology transfers to Iowa.

**chorizon** — Primary source for soil physical and chemical properties as confounders. Key fields: saturated hydraulic conductivity (`ksat_r`) for leaching potential; organic matter (`om_r`) for N cycling; clay content (`claytotal_r`) and texture fractions for water movement; pH (`ph1to1h2o_r`) for N transformation; available water capacity (`awc_r`); bulk density (`dbthirdbar_r`). Aggregate depth-weighted to component level before use.

**corestrictions** — Restrictive layers (kind, hardness, depth) relevant to nitrate transport pathways. Seasonal high water tables are directly relevant to tile drain nitrate loading in Iowa. Restrictive layers limiting root depth affect N uptake and surplus. Requires rolling up to component then map unit level.

**cosoilmoist** — Monthly soil moisture status by depth, linked through comonth. Useful for characterizing drainage seasonality and identifying soils with episaturation or endosaturation conditions that affect denitrification and nitrate leaching pathways.

**comonth** — Monthly flooding and ponding frequency and duration, plus monthly average precipitation and potential ET. Useful for characterizing hydrologic regime and for identifying months with high leaching potential.

**muaggatt** — Pre-aggregated map unit level attributes. The most efficient starting point for county-level spatial joins. Directly relevant fields: water table depth annual minimum (`wtdepannmin`), April–June water table minimum (`wtdepaprjunmin`), drainage class (`drclassdcd`, `drclasswettest`), flooding frequency (`flodfreqdcd`), hydrologic group (`hydgrpdcd`), available water storage at multiple depths (`aws025wta` through `aws0150wta`), and hydric classification (`hydclprs`). Use this table to avoid aggregating from component level when pre-aggregated values are sufficient.

**mapunit** — Required for joining spatial data to tabular data via `mukey`. Contains farmland classification (`farmlndcl`) and Iowa CSR (`iacornsr`) which can serve as soil productivity covariates when `cocropyld` is empty.

**mupolygon** — The spatial geometry layer. Required for area-weighted spatial joins to county boundaries using geopandas.

**cohydriccriteria** — Hydric soil criteria met for each component. Useful for identifying soils with documented saturation conditions, which are mechanistically linked to tile drain nitrate export.

**codiagfeatures** — Diagnostic horizon features including Aquic conditions, which signal reducing conditions relevant to denitrification potential and nitrate leaching patterns.

**legend** and **sacatalog** — Required for filtering data by survey area (state/county). `sacatalog` contains the correlation date, which tells you when each survey area was last updated — important context for interpreting SSURGO as a static snapshot.

**laoverlap** and **muaoverlap** — Used to identify which map units fall within a given county. These are the tables used for county-level area calculation when building area-weighted soil covariates.

**mdstattabs** and **mdstattabcols** — The schema self-documentation tables. Query `mdstattabcols` where `tabphyname` equals any table name to get authoritative column descriptions and units. More reliable than training knowledge.

---

### Tables with Limited or No Relevance to This Research

The following are documented above for completeness but are unlikely to be needed: `chaashto`, `chconsistence`, `chdesgnsuffix`, `chfrags`, `chpores`, `chstruct`, `chstructgrp`, `chtext`, `chtexture`, `chtexturegrp`, `chtexturemod`, `chunified` (horizon detail tables below the level of aggregation needed), `cocanopycover`, `coeplants`, `cotreestomng`, `copwindbreak` (vegetation tables irrelevant to N/water quality), `coforprod`, `coforprodo` (forestry tables), `cosurfmorphgc`, `cosurfmorphhpp`, `cosurfmorphmr`, `cosurfmorphss`, `cogeomordesc` (geomorphic position tables — low priority unless modeling slope-driven N transport explicitly), `cotaxfmmin`, `cotaxmoistcl`, `cotxfmother` (taxonomic detail below what is needed), `cotext`, `mutext`, `legendtext` (narrative text fields, sparsely populated), `featdesc`, `featline`, `featpoint` (spot features), `sdvalgorithm`, `sdvattribute`, `sdvfolder`, `sdvfolderattribute` (Soil Data Viewer application tables, not needed for programmatic access), `distmd`, `distlegendmd`, `distinterpmd` (distribution metadata, internal NRCS bookkeeping), `muline`, `mupoint`, `sapolygon` (spatial tables for non-polygon geometries), `month` (simple lookup), `mdstatdomdet`, `mdstatdommas`, `mdstatidxdet`, `mdstatidxmas`, `mdstatrshipdet`, `mdstatrshipmas` (schema metadata only needed for introspection).

`cocropyld` and `mucropyld` may have some utility as productivity proxies but are empty for Iowa and should be replaced by NASS QuickStats yield data for the actual analysis.

`cointerp` contains NRCS fuzzy logic interpretation scores that could be useful (e.g., the "Prime Farmland" or "Leaching Potential" interpretations) but requires understanding which `mrulename` values correspond to relevant interpretations.
