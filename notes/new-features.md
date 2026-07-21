# New Features to Add

The only USGS-NWIS data currently used in our models are the targets, nitrate concentration. There are thousands of other covariates tracked by the USGS and many other sensors in the streams and rivers which don't track nitrate. These should be used.

The USGS also runs statistics on basins. If you have COMIDs (Common Identifier) for the basins then you can get a whole slew of statistics like tile drainage.

## Potential covariates to try

# Table 1: Core Continuous Sensor Parameters for Baseline Modeling

| Parameter Name | USGS NWIS Code | Data Type | Mechanistic Justification for Nitrate Prediction |
|---|---|---|---|
| Temperature, water | 00010 | Continuous | Exerts primary control on the thermodynamic and kinetic rates of biogeochemical reactions, dictating dissolved oxygen solubility and regulating the metabolic rates of nitrifying bacteria, denitrifying bacteria, and phytoplankton that assimilate nitrate. |
| Dissolved Oxygen | 00300, 00301 | Continuous | Denitrification, the primary removal mechanism for nitrate in groundwater and benthic zones, is strictly an anoxic or suboxic process; high oxygen inhibits denitrification, allowing nitrate to persist conservatively. |
| pH | 00400 | Continuous | Influences the equilibrium of ammonia and ammonium and dictates the overall enzymatic efficiency of the microbial communities driving the nitrogen cycle. |
| Specific Conductance | 00095 | Continuous | Measures the ability of water to transmit an electrical current, serving as a highly effective proxy for total dissolved solids; nitrate is an anion that contributes to ionic strength, making this parameter a powerful continuous surrogate in baseflow-dominated streams and groundwater. |
| Discharge / Gage Height | 00060, 00065 | Continuous | Streamflow and stage are the primary physical drivers of mass transport, capturing the dilution effect during peak overland flow and the delayed flushing effect as subsurface pathways deliver accumulated nitrate to the channel. |

# Table 2: Expanded Continuous Sensor Parameters for Advanced Modeling

| Parameter Name | USGS NWIS Code | Data Type | Mechanistic Justification for Nitrate Prediction |
|---|---|---|---|
| Turbidity | 61028, 63680 | Continuous | Proxies the mobilization of particulate nitrogen and organic matter during high-intensity runoff, capturing the onset of storm-driven hydrologic forcing and allowing models to differentiate between surface dilution and subsurface flushing. |
| Colored Dissolved Organic Matter (fDOM) | 32295, 32322 | Continuous | Measures the availability of dissolved organic carbon, which is the necessary electron donor for heterotrophic denitrification; spikes in fDOM often inversely correlate with nitrate due to enhanced biological reduction. |
| Chlorophyll Fluorescence (fCHL) | 31883, 32320 | Continuous | Provides a near-real-time proxy for total phytoplankton biomass, quantifying the biological assimilation and drawdown of dissolved inorganic nitrogen from the water column. |
| Phycocyanin Fluorescence (fPC) | 32321 | Continuous | Targets specific accessory pigments utilized by cyanobacteria, providing an additional biological uptake proxy in systems prone to harmful algal blooms where cyanobacteria dominate nitrogen assimilation. |
| Precipitation | 00045 | Continuous | The ultimate independent variable driving the hydrologic mobilization of nitrate from the vadose zone; allows temporal models to calculate antecedent moisture conditions, lag times, and intensity-duration thresholds. |

# Table 3: Discrete Sample Parameters for Model Training and Spatial Vulnerability

| Parameter Name | USGS NWIS Code | Data Type | Mechanistic Justification for Nitrate Prediction |
|---|---|---|---|
| Nitrate plus nitrite, dissolved | 00631 | Discrete | The absolute gold standard training variable; quantifies the dissolved, mobile fraction of oxidized nitrogen that poses the greatest risk to drinking water and serves as the dependent variable for regression models. |
| Total Nitrogen / TKN | 00625, 62854 | Discrete | Essential for calibrating models that rely on continuous turbidity; training a model to partition total nitrogen ensures the algorithm accurately accounts for mass conservation during storm events. |
| Suspended-Sediment Concentration (SSC) | 80154 | Discrete | The strictly mandated metric for calibrating turbidity sensors to sediment/particulate mass; unlike TSS, SSC analyzes the entire water-sediment mixture, preventing severe negative biases associated with sand settling. |
| Dissolved Organic Carbon (DOC) | 00681 | Discrete | Provides the empirical baseline required to translate arbitrary in situ fDOM fluorescence units into actionable mass concentrations, serving as a negative predictor for nitrate preservation. |
| Chloride | 00940 | Discrete | A conservative anion that operates as a powerful co-tracer for anthropogenic source tracking, allowing modelers to partition the continuous specific conductance signal. |
| Silica, dissolved | 00955 | Discrete | Serves as an intrinsic tracer for groundwater residence time; older baseflow exhibits higher silica and lower nitrate due to prolonged natural attenuation, defining the hydraulic geometry of the basin. |

## Idea for implementation