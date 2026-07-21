# USGS-NWIS Parameter Code Inventory & Mechanistic Justifications for High-Frequency Nitrate Concentration Prediction

## Executive Summary & Background

In agricultural watersheds across Iowa and the broader U.S. Midwest, nitrate-nitrogen ($NO_3^-$-$N$) loss through subsurface tile drainage and agricultural runoff poses significant challenges for drinking water safety, aquatic ecosystem health, and downstream hypoxia in the Gulf of Mexico. While direct high-frequency optical sensors for nitrate (USGS parameter code `99133`) provide exceptional temporal resolution, their high capital cost, maintenance requirements, biofouling susceptibility, and limited geographic deployment constrain their widespread availability.

To overcome these spatial and financial limitations, water quality data scientists utilize **surrogate regression models** (e.g., Multiple Linear Regression, Random Forests, XGBoost, and Long Short-Term Memory neural networks) to predict continuous nitrate concentrations using more widely deployed, lower-cost continuous sensors and discrete water quality laboratory samples. 

This report provides a comprehensive inventory of USGS National Water Information System (NWIS) parameter codes that possess strong biogeochemical, physical, or hydrological relevance to nitrate dynamics. Parameter codes are categorized into:
1. **Core Continuous Sensor Parameters**: Standard baseline multi-parameter sonde measurements.
2. **Expanded Continuous Sensor Parameters**: Advanced optical, biological, and meteorologic real-time sensors.
3. **Discrete Sample Parameters**: Laboratory-analyzed chemical constituents used for model training, calibration, and hydrogeologic end-member characterization.

---

## 1. Core Continuous Sensor Parameters (Baseline Modeling)

Core continuous parameters represent the foundation of real-time stream monitoring. Multi-parameter water quality sondes deployed at USGS gaging stations frequently record these constituents at 15-minute intervals.

| Parameter Name | USGS NWIS Code | Data Type | Units | Mechanistic Justification for Nitrate Prediction |
|---|---|---|---|---|
| Water Temperature | `00010` | Continuous | °C | Controls thermodynamic and kinetic rates of biogeochemical reactions, dictating dissolved oxygen solubility and regulating metabolic rates of nitrifying bacteria, denitrifying bacteria, and phytoplankton. |
| Dissolved Oxygen | `00300`, `00301` | Continuous | mg/L, % sat | Denitrification is strictly an anoxic or suboxic process; high oxygen inhibits denitrifying reductases, allowing nitrate to persist conservatively in oxic surface waters. |
| pH | `00400` | Continuous | Standard Units | Governs ammonia/ammonium equilibrium ($pK_a \approx 9.25$), dictates enzymatic efficiency of nitrifying bacteria, and shifts during primary production nutrient uptake. |
| Specific Conductance | `00095` | Continuous | µS/cm at 25°C | Serves as a surrogate for total dissolved solids (TDS) and ionic strength; nitrate is a major anion contributing to ionic strength, making specific conductance a powerful proxy during baseflow and tile drain discharge. |
| Discharge / Gage Height | `00060`, `00065` | Continuous | cfs, ft | Primary physical driver of mass transport, capturing dilution during peak surface runoff and delayed flushing as subsurface agricultural tile pathways deliver accumulated nitrate to the channel. |

### Detailed Parameter Mechanics

* **Water Temperature (`00010`)**: Biological nitrogen transformation kinetics follow temperature-dependent Arrhenius relationships. Microbially mediated processes—such as ammonium oxidation by *Nitrosomonas* and nitrite oxidation by *Nitrobacter* (nitrification), as well as nitrate reduction by heterotrophic bacteria (denitrification)—exhibit pronounced seasonal thermal dynamics. In summer, elevated temperatures accelerate benthic denitrification rates, driving seasonal nitrate depletion.
* **Dissolved Oxygen (`00300`, `00301`)**: Heterotrophic denitrifying bacteria utilize nitrate as an alternative terminal electron acceptor only when molecular oxygen is depleted ($DO < 1.0 - 2.0 \text{ mg/L}$). In well-oxygenated streams, denitrification is restricted to deep benthic sediments or hyporheic zones. Furthermore, diel DO oscillations reflect net primary productivity, which directly correlates with photoautotrophic inorganic nitrogen assimilation.
* **pH (`00400`)**: Shifts in stream pH alter the speciation of reduced nitrogen ($NH_3 + H^+ \rightleftharpoons NH_4^+$). Nitrifying bacteria display optimal activity within a narrow pH band ($7.5 - 8.0$). Rapid inorganic carbon uptake by algae during daytime photosynthesis raises stream pH while simultaneously assimilating nitrate, creating an inverse relationship between diel pH peaks and nitrate concentrations.
* **Specific Conductance (`00095`)**: Measures the electrical conductivity of water, which is directly proportional to the concentration of dissolved major ions ($Ca^{2+}, Mg^{2+}, Na^+, Cl^-, SO_4^{2-}, NO_3^-$). In Iowa tile-drained agricultural basins, baseflow enriched with mineral ions and subsurface nitrate exhibits high specific conductance. Rain events cause hysteresis loop patterns where specific conductance drops rapidly due to surface runoff dilution while subsurface tile runoff sustains elevated solute export.
* **Discharge (`00060`) & Gage Height (`00065`)**: Hydrologic transport dictates mass loading ($Load = Flow \times Concentration$). Discharge dynamics allow models to capture transport mechanisms: early hydrograph rising limbs often display a "flushing effect" (mobilization of accumulated vadose zone nitrate), followed by "dilution effects" during high-volume overland flow.

### Extended Mechanistic Analysis of Core Parameters

#### Water Temperature (`00010`)
Water temperature exerts fundamental control over biological nitrogen transformation kinetics through Arrhenius-type enzyme relationships:
$$k = A e^{-\frac{E_a}{R T}}$$

Biological processes involved in nitrogen cycling—including ammonium oxidation by *Nitrosomonas*, nitrite oxidation by *Nitrobacter* (nitrification), and heterotrophic nitrate reduction ($NO_3^- \rightarrow N_2O \rightarrow N_2$, denitrification)—display pronounced thermal sensitivity. In Iowa agricultural streams during warm summer months ($T > 20^\circ\text{C}$), elevated benthic metabolic rates accelerate nitrate removal via denitrification and algal assimilation, leading to seasonal nitrate depletion. Conversely, during freezing winter months, microbial metabolic activity slows dramatically, allowing nitrate transported from agricultural soils to pass conservatively through stream channels. Temperature also governs gas solubility via Henry's Law, directly influencing dissolved oxygen concentrations.

#### Dissolved Oxygen (`00300`, `00301`)
Heterotrophic denitrification is an anaerobic respiration pathway where denitrifying bacteria utilize nitrate as an alternative terminal electron acceptor in place of molecular oxygen:
$$5 CH_2O + 4 NO_3^- + 4 H^+ \rightarrow 5 CO_2 + 2 N_2 + 7 H_2O$$

Because oxygen reduction yields higher free energy than nitrate reduction, oxygen strongly suppresses the synthesis and activity of nitrate reductase enzymes. Denitrification in natural streams occurs almost exclusively in suboxic zones where dissolved oxygen drops below $1.0 - 2.0 \text{ mg/L}$ (e.g., deep benthic sediments, saturated organic hyporheic zones, or stagnant backwaters). In oxic surface stream waters ($DO > 6.0 \text{ mg/L}$), nitrate acts as a conservative solute. Including real-time DO measurements enables predictive models to identify periods when aquatic environments shift toward suboxic conditions favorable for rapid nitrate loss. Furthermore, diel DO swings (daytime photosynthetic oxygen generation vs. nighttime respiratory consumption) provide a direct signal of primary productivity and associated inorganic nitrogen assimilation.

#### pH (`00400`)
Streamwater pH modulates the chemical speciation of reduced nitrogen species through the ammonium-ammonia equilibrium:
$$NH_3 + H^+ \rightleftharpoons NH_4^+ \quad (pK_a \approx 9.25)$$

Nitrifying bacteria that convert ammonium into nitrate exhibit narrow optimal activity ranges ($7.5 < \text{pH} < 8.0$). When stream pH drops significantly, nitrification rates decrease. In eutrophic Iowa streams, daytime photosynthesis by algae and macrophytes rapidly extracts dissolved carbon dioxide ($CO_2$) from the water column, shifting the carbonate equilibrium:
$$HCO_3^- \rightarrow CO_2 + OH^-$$

This photosynthetic drawdown elevates stream pH during daylight hours while primary producers simultaneously assimilate $NO_3^-$ and $NH_4^+$ into organic biomass. As a result, continuous pH fluctuations mirror the biological demand for dissolved inorganic nitrogen.

#### Specific Conductance (`00095`)
Specific conductance measures the ability of water to conduct an electrical current, normalized to $25^\circ\text{C}$, serving as a direct proxy for Total Dissolved Solids (TDS) and total ionic strength ($\mu$):
$$\mu = \frac{1}{2} \sum_{i=1}^{n} c_i z_i^2$$

In agricultural watersheds dominated by subsurface tile drainage (e.g., the Des Moines Lobe in Iowa), tile effluent is enriched with dissolved mineral ions ($Ca^{2+}, Mg^{2+}, HCO_3^-, Cl^-$) and elevated dissolved nitrate ($NO_3^-$). During dry baseflow periods, groundwater and tile drainage sustain high specific conductance alongside elevated nitrate concentrations. When a rain event occurs, rapid overland surface runoff (which is low in dissolved ions) dilutes the stream, causing a sharp drop in specific conductance. In machine learning models, specific conductance serves as a high-frequency surrogate for groundwater/tile water contributions versus surface runoff contributions.

#### Discharge (`00060`) & Gage Height (`00065`)
Streamflow ($Q$) is the overarching physical driver regulating constituent mass flux ($\text{Load} = Q \times C$). High-frequency discharge data capture complex hydrologic hysteresis behavior:
* **Rising Limb (Flushing Effect)**: As precipitation infiltrates agricultural soils, soil pore water rich in accumulated nitrate is pushed into subsurface tile lines and discharged into the stream, causing nitrate concentrations to spike during the early hydrograph rise.
* **Peak & Falling Limb (Dilution Effect)**: As overland flow and direct precipitation dominate total stream discharge, high runoff volumes dilute stream water, driving nitrate concentrations down despite high total mass loading.

Including gage height and discharge allows time-series models (e.g., LSTM or XGBoost with temporal lag features) to resolve hydrologic routing, hysteresis loops, and watershed flushing dynamics.

---

## 2. Expanded Continuous Sensor Parameters (Advanced Modeling)

Advanced sensor deployments incorporate optical fluorescence, turbidity, and local meteorological observations, providing real-time proxies for organic carbon substrate, primary productivity, and hydrologic forcing.

| Parameter Name | USGS NWIS Code | Data Type | Units | Mechanistic Justification for Nitrate Prediction |
|---|---|---|---|---|
| Turbidity | `61028`, `63680` | Continuous | NTU, FNU | Proxies mobilization of particulate organic nitrogen and suspended sediment, allowing models to differentiate between surface runoff (dilution) and subsurface tile drainage (high nitrate). |
| Colored Dissolved Organic Matter (fDOM) | `32295`, `32322` | Continuous | QSE, ppb QSE | Measures optical dissolved organic carbon (DOC), the primary electron donor for heterotrophic denitrification; elevated fDOM indicates carbon-rich suboxic conditions favorable for $NO_3^-$ reduction. |
| Chlorophyll Fluorescence (fCHL) | `31883`, `32320` | Continuous | µg/L, RFU | Continuous proxy for total phytoplankton biomass, quantifying photoautotrophic uptake and seasonal drawdown of dissolved inorganic nitrogen from the water column. |
| Phycocyanin Fluorescence (fPC) | `32321` | Continuous | RFU | Targets accessory pigments in cyanobacteria, providing biological uptake proxies in eutrophic streams prone to harmful algal blooms where cyanobacteria dominate nitrogen assimilation. |
| Precipitation | `00045` | Continuous | Inches | Fundamental independent variable driving hydrologic mobilization of nitrate from vadose zones; enables calculation of antecedent precipitation indices and event lag times. |

### Detailed Parameter Mechanics

* **Turbidity (`61028`, `63680`)**: Turbidity measures light scattering caused by suspended sediments, organic matter, and microscopic organisms. In agricultural systems, surface runoff carries high sediment loads and organic nitrogen (high turbidity) but lower dissolved nitrate relative to subsurface agricultural tile drainage. High-frequency turbidity sensors allow machine learning models to partition hydrograph source pathways.
* **Fluorescent Dissolved Organic Matter - fDOM (`32295`, `32322`)**: Denitrification is a heterotrophic redox process requiring organic carbon ($5 CH_2O + 4 NO_3^- + 4 H^+ \rightarrow 5 CO_2 + 2 N_2 + 7 H_2O$). fDOM fluorometers measure the humic-like fraction of dissolved organic carbon. High fDOM concentrations coupled with low DO indicate an environment with abundant electron donors, predicting accelerated nitrate loss via denitrification.
* **Chlorophyll Fluorescence (`31883`, `32320`) & Phycocyanin (`32321`)**: In nutrient-rich Midwestern streams, summer low-flow conditions foster severe algal blooms. Phytoplankton utilize dissolved inorganic nitrogen ($NO_3^-$ and $NH_4^+$) for cell synthesis, driving pronounced seasonal nitrate depletion. Tracking fluorometric pigments enables models to quantify biological uptake versus hydrologic transport.
* **Precipitation (`00045`)**: Rainfall events initiate the hydrological sequence that flushes accumulated nitrate from agricultural soils into tile drainage networks. Integrating precipitation data allows time-series models (e.g., LSTMs or recurrent neural networks) to calculate antecedent soil moisture conditions, infiltration capacity thresholds, and watershed lag times.

### Extended Mechanistic Analysis of Expanded Parameters

#### Turbidity (`61028`, `63680`)
Optical turbidity sensors measure light scattering ($90^\circ$ angle) caused by suspended sediment, organic detritus, and planktonic organisms. In agricultural landscapes, high turbidity is strongly associated with surface sheet and rill erosion carrying soil particles and particulate organic nitrogen (PON). Subsurface tile drainage, conversely, exports clear water with high dissolved nitrate ($NO_3^-$) and minimal turbidity. Therefore, inverse relationships between turbidity spikes and dissolved nitrate help machine learning models differentiate topsoil erosion events from subsurface tile discharge events.

#### Fluorescent Dissolved Organic Matter - fDOM (`32295`, `32322`)
fDOM fluorometers measure the ultraviolet excitation and blue fluorescence emission of humic-like and fulvic-like dissolved organic matter. Dissolved Organic Carbon (DOC) serves as the indispensable carbon and energy source (electron donor) for heterotrophic denitrifying bacteria. In many agricultural streams, denitrification is carbon-limited rather than nitrate-limited. High fDOM signals indicate an abundance of labile organic carbon; when paired with low dissolved oxygen (`00300`), high fDOM signals indicate optimal biogeochemical conditions for microbial $NO_3^-$ reduction to $N_2$ gas.

#### Chlorophyll Fluorescence (`31883`, `32320`) & Phycocyanin (`32321`)
In situ optical fluorometers measure chlorophyll-$a$ and phycocyanin fluorescence to provide real-time estimates of total phytoplankton and cyanobacterial biomass. During sunny, low-flow summer periods, dense algal and cyanobacterial blooms consume large quantities of dissolved inorganic nitrogen ($NO_3^-$ and $NH_4^+$) for cell synthesis according to the Redfield stoichiometry:
$$106 CO_2 + 16 NO_3^- + HPO_4^{2-} + 122 H_2O + 18 H^+ \rightarrow \{C_{106}H_{263}O_{110}N_{16}P\} + 138 O_2$$

Continuous fCHL and fPC sensors enable predictive models to track the timing and magnitude of biological nitrogen assimilation, capturing rapid non-hydrologic drops in stream nitrate concentrations.

#### Precipitation (`00045`)
Local rain gages provide direct measurements of precipitation depth and intensity. Rainfall is the ultimate hydrological trigger driving soil infiltration, vadose zone wetting, groundwater table elevation, and tile drain activation. Integrating precipitation data into machine learning pipelines allows models to construct Antecedent Precipitation Indices (API), calculate soil moisture state variables, and model time-lagged nitrate delivery following major rainfall events.

---

## 3. Discrete Sample Parameters (Model Training & Spatial Vulnerability)

Discrete parameters are obtained via physical water samples collected during routine USGS field trips or automated storm samplers and analyzed in certified laboratories. While not continuous, these measurements are essential for model training, target label generation, sensor calibration, and hydrogeologic end-member mixing analysis (EMMA).

| Parameter Name | USGS NWIS Code | Data Type | Units | Mechanistic Justification for Nitrate Prediction |
|---|---|---|---|---|
| Nitrate + Nitrite, Dissolved | `00631` | Discrete | mg/L as N | Primary target/dependent variable for training and validating surrogate regression algorithms; represents the mobile oxidized inorganic nitrogen pool. |
| Total Nitrogen / TKN | `00625`, `62854` | Discrete | mg/L as N | Essential for establishing total nitrogen mass balance and calibrating turbidity models to partition dissolved inorganic nitrogen vs. particulate/organic nitrogen. |
| Suspended-Sediment Concentration (SSC) | `80154` | Discrete | mg/L | Analytical benchmark required to calibrate continuous turbidity sensors to true sediment mass, preventing sand-settling bias inherent in TSS methods. |
| Dissolved Organic Carbon (DOC) | `00681` | Discrete | mg/L as C | Laboratory benchmark required to calibrate continuous in situ fDOM fluorescence units (QSE/RFU) into absolute organic carbon mass concentrations. |
| Chloride, Dissolved | `00940` | Discrete | mg/L | Conservative non-reactive anion co-tracer used to separate anthropogenic source signatures (potash fertilizer, livestock manure, road salt) from specific conductance signals. |
| Silica, Dissolved | `00955` | Discrete | mg/L as SiO2 | Intrinsic tracer for groundwater residence time; deep baseflow exhibits elevated silica and lower nitrate due to prolonged natural attenuation in reduced aquifers. |

### Detailed Parameter Mechanics

* **Nitrate + Nitrite (`00631`)**: In oxygenated surface waters, nitrite ($NO_2^-$) concentrations are negligible ($<0.01 \text{ mg/L}$), making parameter code `00631` practically equivalent to nitrate ($NO_3^-$). This parameter serves as the fundamental ground-truth target ($Y$-variable) when training supervised machine learning algorithms against continuous sensor features ($X$-variables).
* **Total Nitrogen & TKN (`00625`, `62854`)**: Total Kjeldahl Nitrogen (TKN) measures organic nitrogen plus ammonia ($NH_3 + NH_4^+$). Subtracting TKN from Total Nitrogen yields dissolved oxidized nitrogen. Combining these measurements ensures that machine learning models adhere to mass-conservation principles during high-turbidity storm events when nitrogen shifts between particulate organic and dissolved inorganic phases.
* **Suspended-Sediment Concentration - SSC (`80154`)**: The USGS mandates SSC over Total Suspended Solids (TSS) for open-water sediment modeling because SSC measures the entire water-sediment volume in the sample container, eliminating bias caused by coarse sand settling. SSC laboratory data are required to establish robust rating curves for optical turbidity sensors.
* **Dissolved Organic Carbon - DOC (`00681`)**: In situ fDOM sensor signals suffer from temperature quenching, inner-filter effects, and turbidity interference. Discrete laboratory DOC measurements provide the analytical calibration baseline to convert raw fDOM relative fluorescence units into quantitative DOC mass metrics.
* **Chloride (`00940`)**: Chloride acts as a conservative tracer because it does not undergo significant biological transformation or sorptive attenuation in stream channels. By monitoring $Cl^- / NO_3^-$ mass ratios, models can distinguish between synthetic agricultural fertilizer inputs ($high \ NO_3^-, low \ Cl^-$), animal manure / septic leachates ($high \ NO_3^-, high \ Cl^-$), and groundwater baseflow.
* **Dissolved Silica (`00955`)**: Weathering of silicate minerals enriches deep groundwater with dissolved silica ($SiO_2$). Because surface runoff contains minimal silica, $SiO_2$ concentrations indicate groundwater residence time. Deep baseflow with high silica often experiences prolonged contact with suboxic aquifer matrices, resulting in low nitrate due to natural attenuation.

### Extended Mechanistic Analysis of Discrete Parameters

#### Nitrate plus Nitrite, Dissolved (`00631`)
In oxic surface waters, nitrite ($NO_2^-$) concentrations are typically negligible ($<0.01 \text{ mg/L}$ as N) due to rapid microbially mediated oxidation to nitrate ($NO_3^-$) by *Nitrobacter*. Consequently, parameter `00631` represents the gold-standard ground-truth target variable ($Y$) used in supervised machine learning regression. Discrete `00631` measurements collected across varying seasons and flow regimes provide the target labels required to train and validate continuous surrogate models.

#### Total Nitrogen (`62854`) & Total Kjeldahl Nitrogen (`00625`)
Total Nitrogen (TN) encompasses all forms of nitrogen:
$$\text{TN} = \text{NO}_3^- + \text{NO}_2^- + \text{NH}_4^+ + \text{Organic N}$$

Total Kjeldahl Nitrogen (TKN, `00625`) measures organic nitrogen plus ammonia ($\text{Organic N} + \text{NH}_4^+$). Subtracting TKN from TN yields dissolved oxidized nitrogen ($\text{NO}_3^- + \text{NO}_2^-$). These discrete laboratory fractions enable data scientists to verify mass conservation principles in predictive models, ensuring algorithms accurately partition nitrogen between particulate organic phases during storm events and dissolved inorganic phases during baseflow.

#### Suspended-Sediment Concentration - SSC (`80154`)
The USGS explicitly mandates Suspended-Sediment Concentration (`80154`) over Total Suspended Solids (`00530`, TSS) for open-water sediment modeling. SSC analysis utilizes the entire water-sediment volume within the sample container, whereas TSS relies on taking an aliquot with a pipette—a method that systematically undercounts coarse sand particles. Discrete SSC lab values are required to build robust rating curves that convert continuous optical turbidity measurements (`61028`/`63680`) into true suspended sediment mass, which in turn helps isolate particulate nitrogen loads from dissolved nitrate loads.

#### Dissolved Organic Carbon - DOC (`00681`)
In situ fDOM fluorometer readings are subject to environmental interferences, including temperature quenching, inner-filter absorption, and particle scattering. Discrete laboratory DOC measurements (`00681`), analyzed via high-temperature catalytic combustion, provide the empirical ground truth needed to correct raw fDOM sensor signals (QSE or RFU) into quantitative DOC mass concentrations.

#### Chloride, Dissolved (`00940`)
Chloride ($Cl^-$) is a conservative, non-reactive anion that does not undergo significant biological transformation, volatilization, or precipitation in surface water systems. Because major agricultural nitrogen sources carry distinct $Cl^- / NO_3^-$ stoichiometric ratios, discrete chloride measurements allow models to identify distinct nutrient origins:
* **Synthetic Nitrogen Fertilizer (e.g., Anhydrous Ammonia, UAN)**: High $NO_3^-$, Low $Cl^-$
* **Livestock Manure & Septic System Effluent**: High $NO_3^-$, High $Cl^-$
* **Potash Fertilizer ($KCl$) & Winter Deicing Salt**: Low $NO_3^-$, Very High $Cl^-$

By coupling discrete chloride data with continuous specific conductance (`00095`), models can separate ionic conductivity shifts into agricultural drainage, animal waste, or road salt components.

#### Silica, Dissolved (`00955`)
Dissolved silica ($SiO_2$) originates from the chemical weathering of silicate minerals in soils and bedrock. Surface runoff carries minimal silica, whereas deep groundwater baseflow exhibits elevated silica concentrations due to extended mineral contact time. In hydrogeology, silica serves as a natural tracer for water residence time. Deep baseflow with high silica typically exhibits low nitrate levels because prolonged storage in reduced subterranean aquifers allows microbial denitrification to consume nitrate. Discrete silica measurements allow data scientists to construct End-Member Mixing Analysis (EMMA) models that calculate the exact percentage of streamflow originating from deep groundwater versus shallow agricultural tile drainage.

---

## 4. Python Implementation Structure

To integrate these parameter codes directly into Python-based USGS NWIS data ingestion pipelines (e.g., using `dataretrieval` or `requests` to query the USGS Water Services REST API), the code dictionary structure below organizes all parameters:

```python
# Core Continuous Parameters (Baseline Sondes)
CORE_CONTINUOUS_PCODES = {
    "00010": "temp_water",              # Water temperature (°C)
    "00300": "diss_oxy_con",            # Dissolved oxygen concentration (mg/L)
    "00301": "diss_oxy_sat",            # Dissolved oxygen saturation (%)
    "00400": "ph",                      # pH (standard units)
    "00095": "spec_cond",               # Specific conductance (µS/cm at 25°C)
    "00060": "discharge",               # Stream discharge (ft³/s)
    "00065": "stage",                   # Gage height / stage (ft)
}

# Expanded Continuous Parameters (Optical, Biological, Meteorologic)
EXPANDED_CONTINUOUS_PCODES = {
    "61028": "turbidity_ntu",           # Turbidity (NTU)
    "63680": "turbidity_fnu",           # Turbidity (FNU)
    "32295": "fdom_qse",                # Colored Dissolved Organic Matter (QSE)
    "32322": "fdom_ppb",                # Colored Dissolved Organic Matter (ppb QSE)
    "31883": "chlorophyll_a",           # Chlorophyll a, fluorescence (µg/L)
    "32320": "chlorophyll_rfu",         # Chlorophyll fluorescence (RFU)
    "32321": "phycocyanin_rfu",         # Phycocyanin fluorescence (RFU)
    "00045": "precipitation",           # Total precipitation (inches)
}

# Discrete Laboratory Parameters (Model Training & Calibration)
DISCRETE_SAMPLE_PCODES = {
    "00631": "nitrate_nitrite_diss",    # Nitrate plus nitrite, dissolved (mg/L as N)
    "00625": "tkn_unfiltered",          # Total Kjeldahl Nitrogen (mg/L as N)
    "62854": "total_nitrogen_diss",     # Total Nitrogen, dissolved (mg/L as N)
    "80154": "suspended_sediment_conc", # Suspended-Sediment Concentration (mg/L)
    "00681": "organic_carbon_diss",     # Dissolved Organic Carbon (DOC) (mg/L)
    "00940": "chloride_diss",           # Chloride, dissolved (mg/L)
    "00955": "silica_diss",             # Silica, dissolved (mg/L as SiO2)
}

# Unified USGS-NWIS Nitrate Surrogate Parameter Dictionary
ALL_NITRATE_PREDICTION_PCODES = {
    **CORE_CONTINUOUS_PCODES,
    **EXPANDED_CONTINUOUS_PCODES,
    **DISCRETE_SAMPLE_PCODES,
}
```

---

## 5. References & Scientific Literature

1. **Rasmussen, P. P., Gray, J. R., Glysson, G. D., & Ziegler, A. C. (2009).** *Guidelines and procedures for computing continuous slurry and sediment loads using surrogate regression models.* U.S. Geological Survey Techniques and Methods, Book 3, Chapter C4, 48 p.
2. **Pellerin, B. A., Bergamaschi, B. A., Downing, B. D., Saraceno, J. F., Garrett, J. A., & Boss, E. S. (2014).** *Optical techniques for real-time continuous water quality monitoring.* U.S. Geological Survey Fact Sheet 2014-3038, 4 p.
3. **Jones, C. S., Davis, C. A., Drake, C. W., Schilling, K. E., Debionne, S. H., Giles, E. T., & Weber, L. J. (2018).** *Iowa statewide stream nitrate network: Data collection, processing, and public availability.* Earth System Science Data, 10(1), 503-515.
4. **Schilling, K. E., & Zhang, Y. K. (2004).** *Baseflow contribution to nitrate-nitrogen export from a large agricultural watershed, USA.* Journal of Hydrology, 295(1-4), 305-316.
5. **Groffman, P. M., Altabet, M. A., Böhlke, J. K., Butterbach-Bahl, K., David, M. B., Firestone, M. K., ... & Yee, C. (2006).** *Methods for measuring denitrification: diverse approaches to a difficult problem.* Ecological Applications, 16(6), 2091-2122.
6. **Miller, M. P., Tesoriero, A. J., Capel, P. D., Pellerin, B. A., Hyer, K. E., & Burns, D. A. (2016).** *Quantifying the seasonal importance of nitrate retention and loss in agricultural streams using high-frequency sensor measurements.* Biogeochemistry, 130(1), 69-83.
7. **Crawford, J. T., Stets, E. G., Loken, L. C., & Stanley, E. H. (2017).** *High-frequency dynamic patterns of dissolved organic carbon and nitrate in an agricultural stream.* Water Resources Research, 53(7), 5612-5629.
8. **Hirsch, R. M., Moyer, D. L., & Archfield, S. A. (2010).** *Weighted Regressions on Time, Discharge, and Season (WRTDS), with an application to trend analysis in the Chicago River.* Journal of the American Water Resources Association, 46(5), 857-880.
9. **Gray, J. R., Glysson, G. D., Turrios, R. E., & Schwarz, G. E. (2000).** *Comparability of suspended-sediment concentration and total suspended solids data.* U.S. Geological Survey Water-Resources Investigations Report 00-4191, 28 p.
10. **Downing, B. D., Boss, E., Bergamaschi, B. A., Fleck, J. A., Buer, A. L., Yost, R., ... & Kraus, T. E. (2012).** *Defining biological and geochemical drivers of dissolved organic matter fluorescence in aquatic ecosystems.* Environmental Science & Technology, 46(16), 8820-8828.